"""作者：Wangyuan；日期：2026-10-29；职责：本地收文、任务、案件、交接与人工复核账本。"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import closing
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from text_rules import analyze


def canonical(value: Any) -> str:
    """生成稳定JSON，拒绝非有限数。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def instant(value: str) -> datetime:
    """只接受带时区的时刻，不猜本机时区。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时刻必须带时区")
    return parsed.astimezone(timezone.utc)


def extract_text(filename: str, payload: bytes, limit: int = 5_000_000) -> str:
    """安全读取UTF-8文本或DOCX正文；不解压到磁盘，不执行宏或外部关系。"""
    if not payload or len(payload) > limit:
        raise ValueError("文件为空或超过预算")
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        text = payload.decode("utf-8-sig")
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(x.file_size for x in entries) > limit * 8:
                raise ValueError("压缩包超出展开预算")
            for item in entries:
                parts = item.filename.replace("\\", "/").split("/")
                if ".." in parts or item.filename.startswith("/") or item.file_size > max(1, item.compress_size) * 200:
                    raise ValueError("压缩包路径或压缩比异常")
                if item.filename.endswith(".rels"):
                    relationships = ElementTree.fromstring(archive.read(item))
                    if any(x.attrib.get("TargetMode") == "External" for x in relationships):
                        raise ValueError("不接受外部文档关系")
            raw = archive.read("word/document.xml")
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("不接受XML实体")
            tree = ElementTree.fromstring(raw)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for paragraph in tree.findall(".//w:p", ns):
                parts = []
                for node in paragraph.iter():
                    if node.tag == "{" + ns["w"] + "}t": parts.append(node.text or "")
                    elif node.tag == "{" + ns["w"] + "}tab": parts.append("\t")
                    elif node.tag in {"{" + ns["w"] + "}br", "{" + ns["w"] + "}cr"}: parts.append("\n")
                paragraphs.append("".join(parts))
            text = "\n".join(paragraphs)
    elif suffix == ".pdf":
        from limited_pdf import read_pdf_literals
        text = read_pdf_literals(payload)
    else:
        raise ValueError("只支持文本、DOCX或受限PDF；其他格式须提供校对正文")
    if len(text) > 200_000:
        raise ValueError("正文超出字符预算")
    return text


def candidates(text: str, received_on: str, calendar: dict | None = None) -> dict:
    """从通用中文正文提取义务/期限/跨段候选，保留引文及人工确认状态。"""
    return analyze(text, received_on=date.fromisoformat(received_on), calendar=calendar)


class Ledger:
    """SQLite本地账本，修订采用乐观版本并使旧复核失效。"""

    def __init__(self, path: str | Path) -> None:
        """仅使用显式数据库路径；不发现原应用配置。"""
        self.path = str(path)
        with closing(self._connect()) as db, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
              source_key TEXT UNIQUE NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              record_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS counters(month TEXT PRIMARY KEY, number INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS attachments(digest TEXT PRIMARY KEY, filename TEXT NOT NULL, content BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(key TEXT PRIMARY KEY, record_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        """事务连接使用有限等待，错误会回滚。"""
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _write(self, key: str, payload: dict, action: str, actor: str, operation: Any) -> dict:
        """同幂等键同内容重试返回原结果，不同内容拒绝。"""
        if not key or not actor:
            raise ValueError("操作键和操作者不能为空")
        fingerprint = hashlib.sha256(canonical([action, actor, payload]).encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("幂等键内容冲突")
                return json.loads(previous["result"])
            result = operation(db)
            db.execute("INSERT INTO operations VALUES(?,?,?)", (key, fingerprint, canonical(result)))
            db.execute("INSERT INTO audit(record_id,action,actor,body) VALUES(?,?,?,?)",
                       (result["id"], action, actor, canonical(result)))
            return result

    def create(self, kind: str, source_key: str, title: str, *, received_on: str,
               actor: str, operation_key: str, due_at: str | None = None,
               owner: str = "", text: str = "") -> dict:
        """创建收文、任务、案件或交接，按月份原子分配序号。"""
        if kind not in {"receipt", "task", "case", "handover", "meeting"} or not title.strip() or not source_key:
            raise ValueError("种类、标题或来源标识无效")
        received_on=date.fromisoformat(received_on).isoformat()
        if due_at is not None:
            instant(due_at)
        payload = dict(kind=kind, source_key=source_key, title=title.strip(), received_on=received_on,
                       due_at=due_at, owner=owner, text=text)
        def operation(db: sqlite3.Connection) -> dict:
            """在调用者持有的SQLite事务中完成原子状态更新。"""
            old = db.execute("SELECT body FROM records WHERE source_key=?", (source_key,)).fetchone()
            if old:
                value = json.loads(old[0])
                if value["initial"] != payload:
                    raise ValueError("同一来源内容冲突，请显式修订")
                return value
            month = received_on[:7]
            db.execute("INSERT INTO counters VALUES(?,1) ON CONFLICT(month) DO UPDATE SET number=number+1", (month,))
            number = db.execute("SELECT number FROM counters WHERE month=?", (month,)).fetchone()[0]
            identifier = hashlib.sha256(source_key.encode()).hexdigest()[:24]
            value = {**payload, "id": identifier, "number": f"{month}-{number:04d}", "initial": payload,
                     "version": 1, "state": "open", "evidence": [], "confirmations": [], "history": []}
            db.execute("INSERT INTO records VALUES(?,?,?,?,1)", (identifier, kind, source_key, canonical(value)))
            return value
        return self._write(operation_key, payload, "create", actor, operation)

    def get(self, identifier: str) -> dict:
        """按身份读取，缺记录不伪造空对象。"""
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT body FROM records WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError(identifier)
        return json.loads(row[0])

    def change(self, identifier: str, expected_version: int, *, action: str, actor: str,
               operation_key: str, values: dict | None = None) -> dict:
        """执行修订、证据追加、人工确认与完成；旧确认不能跨版本沿用。"""
        values = values or {}
        if action not in {"revise", "evidence", "confirm", "start", "complete", "reopen"}:
            raise ValueError("不支持的状态动作")
        payload = dict(id=identifier, version=expected_version, action=action, values=values)
        def operation(db: sqlite3.Connection) -> dict:
            """在调用者持有的SQLite事务中完成原子状态更新。"""
            row = db.execute("SELECT body,version FROM records WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row["version"] != expected_version:
                raise ValueError("版本冲突")
            value = json.loads(row["body"])
            if action == "revise":
                if not values or set(values) - {"title", "owner", "due_at", "deadline_date", "text"}:
                    raise ValueError("修订字段不在白名单")
                if "due_at" in values and values["due_at"] is not None:
                    instant(values["due_at"])
                if values.get("deadline_date"):
                    date.fromisoformat(values["deadline_date"])
                if "title" in values and (not isinstance(values["title"],str) or not values["title"].strip()):
                    raise ValueError("标题不能为空")
                value.update(values)
                value["confirmations"] = []
                value["state"] = "needs_review"
            elif action == "evidence":
                if not values.get("reference") or not values.get("text"):
                    raise ValueError("证据须有来源与内容")
                digest = hashlib.sha256(canonical(values).encode()).hexdigest()
                if any(e["digest"] == digest for e in value["evidence"]):
                    return value
                value["evidence"].append({**values, "digest": digest})
                value["confirmations"] = []
                value["state"] = "needs_review"
            elif action == "confirm":
                if value["state"]=="completed":raise ValueError("已完成事项须先显式重新开启")
                if values.get("confirmed") is not True:
                    raise ValueError("必须显式人工确认")
                value["confirmations"].append({"actor": actor, "content_version": expected_version,
                                                "reference": values.get("reference", "")})
                value["state"] = "reviewed"
            elif action == "start":
                if value["state"] not in {"open", "reviewed"}:
                    raise ValueError("当前状态不能开始")
                value["state"] = "active"
            elif action == "complete":
                if not value["evidence"] or not value["confirmations"] or value["state"] not in {"reviewed", "active"}:
                    raise ValueError("完成须有当前证据与人工复核")
                value["state"] = "completed"
            else:
                if value["state"] != "completed":
                    raise ValueError("只有完成事项可重新开启")
                value["state"], value["confirmations"] = "needs_review", []
            value["history"].append({"version": expected_version, "action": action, "actor": actor})
            value["version"] += 1
            db.execute("UPDATE records SET body=?,version=? WHERE id=?", (canonical(value), value["version"], identifier))
            db.execute("DELETE FROM drafts WHERE record_id=?", (identifier,))
            return value
        return self._write(operation_key, payload, action, actor, operation)

    def attach(self, filename: str, payload: bytes) -> str:
        """以哈希存储附件字节，不使用文件名拼接写入路径。"""
        if not payload or len(payload) > 5_000_000 or Path(filename).name != filename:
            raise ValueError("附件名或大小无效")
        digest = hashlib.sha256(payload).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO attachments VALUES(?,?,?)", (digest, filename, payload))
        return digest

    def import_candidates(self, document_id: str, text: str, tasks: list[dict], *,
                          received_on: str, actor: str, operation_key: str,
                          confirmed: bool) -> dict:
        """人工确认后原子导入有原文锚点的任务；失败回滚，重试不重复。"""
        if confirmed is not True or not document_id or not tasks:
            raise ValueError("导入须有文档身份、候选和人工确认")
        received_on=date.fromisoformat(received_on).isoformat()
        if len(text) > 200_000 or len(tasks) > 1000:
            raise ValueError("导入预算超限")
        source_hash = hashlib.sha256(text.encode()).hexdigest()
        normalized = []
        for task in tasks:
            if task.get("deadline_time"):raise ValueError("候选含截止时刻，须显式时区后人工处理，不能降级为日期")
            quote = task.get("source_quote")
            description = task.get("description")
            if not isinstance(quote, str) or not quote or quote not in text or not isinstance(description, str) or not description.strip():
                raise ValueError("候选缺少可验证的原文锚点")
            if task.get("deadline"):
                date.fromisoformat(task["deadline"])
            normalized.append({"description": description, "source_quote": quote,
                               "deadline": task.get("deadline"), "owner": task.get("owner", "")})
        if len({canonical(x) for x in normalized}) != len(normalized):
            raise ValueError("候选重复")
        payload = {"document_id": document_id, "source_hash": source_hash, "tasks": normalized, "received_on": received_on}
        def operation(db: sqlite3.Connection) -> dict:
            """在调用者持有的SQLite事务中完成原子状态更新。"""
            results = []
            for index, task in enumerate(normalized):
                source_key = f"import:{document_id}:{source_hash}:{index}"
                identifier = hashlib.sha256(source_key.encode()).hexdigest()[:24]
                previous = db.execute("SELECT body FROM records WHERE source_key=?", (source_key,)).fetchone()
                if previous:
                    saved = json.loads(previous[0])
                    if saved["import_candidate"] != task:
                        raise ValueError("同一来源候选变更，请显式修订")
                    results.append(saved); continue
                month = received_on[:7]
                db.execute("INSERT INTO counters VALUES(?,1) ON CONFLICT(month) DO UPDATE SET number=number+1", (month,))
                number = db.execute("SELECT number FROM counters WHERE month=?", (month,)).fetchone()[0]
                record = {"id": identifier, "kind": "task", "source_key": source_key, "title": task["description"],
                          "received_on": received_on, "due_at": None, "deadline_date": task["deadline"],
                          "owner": task["owner"], "text": task["source_quote"], "number": f"{month}-{number:04d}",
                          "version": 1, "state": "open", "evidence": [], "confirmations": [], "history": [],
                          "initial": payload, "import_candidate": task, "source_hash": source_hash}
                db.execute("INSERT INTO records VALUES(?,?,?,?,1)", (identifier, "task", source_key, canonical(record)))
                results.append(record)
            return {"id": f"import:{document_id}", "records": results, "source_hash": source_hash, "state": "imported"}
        return self._write(operation_key, payload, "import_candidates", actor, operation)

    def draft(self, identifier: str, expected_version: int, template: str) -> dict:
        """只生成本地消息草稿，固定字段白名单且不含发送器。"""
        import string
        fields = {field for _, field, _, _ in string.Formatter().parse(template) if field}
        if fields - {"title", "owner", "due_at", "state", "number"}:
            raise ValueError("模板字段不在白名单")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM records WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            record = json.loads(row[0])
            if record["version"] != expected_version:
                raise ValueError("草稿版本冲突")
            body = {"record_id": identifier, "version": expected_version, "text": template.format_map(record), "state": "local_draft"}
            key = hashlib.sha256(canonical(body).encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO drafts VALUES(?,?,?,?)", (key, identifier, expected_version, canonical(body)))
            return {"key": key, **body}

    def report(self, now: str, month: str | None = None) -> dict:
        """生成待办、逾期、疑似重复标题及审计导出。"""
        moment = instant(now)
        with closing(self._connect()) as db, db:
            rows = [json.loads(x[0]) for x in db.execute("SELECT body FROM records ORDER BY id")]
            audit = [dict(x) for x in db.execute("SELECT sequence,record_id,action,actor FROM audit ORDER BY sequence")]
        if month:
            date.fromisoformat(month + "-01")
            rows = [x for x in rows if x["received_on"].startswith(month)]
        for row in rows:
            expired = bool(row["due_at"] and instant(row["due_at"]) < moment)
            if row.get("deadline_date") and not row["due_at"]:
                expired = date.fromisoformat(row["deadline_date"]) < datetime.fromisoformat(now).date()
            row["overdue"] = row["state"] != "completed" and expired
        titles: dict[str, list[str]] = {}
        for row in rows:
            titles.setdefault("".join(row["title"].split()), []).append(row["id"])
        return {"records": rows, "overdue_count": sum(x["overdue"] for x in rows),
                "suspected_duplicates": [v for v in titles.values() if len(v) > 1], "audit": audit}


def coverage(items: list[dict], requirements: list[dict], cutoff: str) -> dict:
    """检查会议/案件材料的类别、日期和角色覆盖及过期状态。"""
    moment = instant(cutoff)
    gaps = []
    for required in requirements:
        matches = [x for x in items if all(x.get(k) == v for k, v in required.items())]
        usable = [x for x in matches if instant(x["captured_at"]) <= moment and
                  (not x.get("valid_until") or instant(x["valid_until"]) >= moment)]
        if not usable:
            gaps.append({"required": required, "reason": "missing_or_stale"})
    return {"complete": not gaps, "gaps": gaps,
            "fingerprint": hashlib.sha256(canonical([items, requirements, cutoff]).encode()).hexdigest()}


def confirmed_reason(catalog: list[str], candidate: dict, evidence: dict[str, str]) -> dict:
    """验证原因候选及引用，未经人工确认不升级为决定。"""
    if candidate.get("reason") not in catalog or not candidate.get("quotes"):
        raise ValueError("原因或引用缺失")
    for quote in candidate["quotes"]:
        if quote.get("source") not in evidence or not quote.get("text") or quote["text"] not in evidence[quote["source"]]:
            raise ValueError("引用没有原文证据")
    return {**candidate, "state": "confirmed" if candidate.get("human_confirmed") is True else "candidate"}
