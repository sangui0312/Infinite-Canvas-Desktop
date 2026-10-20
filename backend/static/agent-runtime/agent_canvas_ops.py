"""Atomic, reversible Agent edits to the existing canvas JSON format."""
import copy
import hashlib
import json
import math
import os
import tempfile
import time

from fastapi import HTTPException


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def undo_node_state(node):
    """Image loader caches are observations, not edits to a delivered resource."""
    result = copy.deepcopy(node)
    if isinstance(result, dict):
        for image in result.get("images", []):
            for key in ("_naturalSizeLoading", "natural_w", "natural_h", "layout_w", "layout_h", "preview_w", "preview_h"):
                image.pop(key, None)
    return result


class CanvasOperations:
    def __init__(self, path_for, lock):
        self.path_for, self.lock = path_for, lock

    def read(self, canvas_id):
        try:
            with open(self.path_for(canvas_id), encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            raise HTTPException(404, "画布不存在")

    def _write(self, canvas):
        path = self.path_for(canvas["id"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".canvas-agent-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(canvas, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, canvas, expected_revision=None, base_updated_at=0, _preserve_server=True):
        with self.lock:
            path = self.path_for(canvas["id"])
            current = self.read(canvas["id"]) if os.path.exists(path) else {}
            revision = int(current.get("revision", 0))
            supplied = expected_revision if expected_revision is not None else canvas.get("revision")
            stale = supplied is not None and int(supplied) != revision
            stale |= bool(base_updated_at and int(base_updated_at) < int(current.get("updated_at", 0)))
            if stale:
                raise HTTPException(409, {"message": "画布已更新，请重新读取后应用改动。", "canvas": current, "revision": revision})
            for field in ("agent_operation_receipts", "agent_deleted_nodes"):
                if _preserve_server and field in current:
                    canvas[field] = copy.deepcopy(current[field])
            deleted = canvas.get("agent_deleted_nodes", {})
            if any(node.get("id") in deleted for node in canvas.get("nodes", [])):
                raise HTTPException(409, {"message": "部分节点已经撤销，请重新读取画布。", "canvas": current})
            canvas["revision"] = revision + 1
            canvas["updated_at"] = max(int(time.time() * 1000), int(current.get("updated_at", 0)) + 1)
            self._write(canvas)
            return canvas

    def apply(self, canvas_id, operation_id, operation):
        if not operation_id or len(operation_id) > 240:
            raise HTTPException(400, "无效的画布操作编号")
        digest = fingerprint(operation)
        with self.lock:
            canvas = self.read(canvas_id)
            if canvas.get("kind") != "smart":
                raise HTTPException(400, "Agent 仅支持智能画布")
            receipts = canvas.setdefault("agent_operation_receipts", {})
            if operation_id in receipts:
                old = receipts[operation_id]
                if old["digest"] != digest:
                    raise HTTPException(409, "操作编号已用于其他参数")
                return {"canvas": canvas, "node_ids": old["node_ids"], "operation_id": operation_id, "undone": bool(old.get("undone"))}
            original = {node["id"]: copy.deepcopy(node) for node in canvas.get("nodes", [])}
            original_edges = copy.deepcopy(canvas.get("connections", []))
            self._apply(canvas, operation_id, operation)
            actual = {node["id"]: node for node in canvas.get("nodes", [])}
            changed = sorted(key for key in original.keys() | actual.keys() if original.get(key) != actual.get(key))
            before, after = {}, {}
            for key in changed:
                old, new = original.get(key), actual.get(key)
                if old is None or new is None:
                    before[key], after[key] = old, copy.deepcopy(new)
                else:
                    fields = old.keys() | new.keys()
                    before[key] = {field: {"exists": field in old, "value": old.get(field)} for field in fields if old.get(field) != new.get(field) or (field in old) != (field in new)}
                    after[key] = {field: {"exists": field in new, "value": new.get(field)} for field in before[key]}
            edges = canvas.get("connections", [])
            added_edges = [edge for edge in edges if edge not in original_edges]
            receipts[operation_id] = {"digest": digest, "node_ids": changed, "before": before, "after": after,
                                      "created_ids": [key for key in changed if key not in original],
                                      "added_edges": added_edges, "undone": False}
            self.save(canvas, expected_revision=canvas.get("revision", 0), _preserve_server=False)
            return {"canvas": canvas, "node_ids": changed, "operation_id": operation_id}

    def _apply(self, canvas, op_id, op):
        nodes = canvas.setdefault("nodes", [])
        by_id = {node["id"]: node for node in nodes}
        edges = canvas.setdefault("connections", [])
        kind = op.get("type")
        stable = hashlib.sha256(op_id.encode()).hexdigest()[:24]

        def require(ids):
            if not ids or any(key not in by_id for key in ids):
                raise HTTPException(409, "引用节点已不存在，请更新计划")
            return [by_id[key] for key in ids]

        def point():
            # Put results to the right of existing work without rearranging it.
            return max((float(node.get("x", 0)) + float(node.get("w", 320)) for node in nodes), default=0) + 100, 120

        def edge(source, target, edge_kind="flow"):
            if source == target or edge_kind not in {"flow", "history"}:
                raise HTTPException(400, "不支持此连线")
            require([source, target])
            if any(item.get("from") == source and item.get("to") == target and item.get("kind", "flow") == edge_kind for item in edges):
                return
            # Reject cycles for execution flow.
            pending, seen = [target], set()
            while pending:
                current = pending.pop()
                if current == source:
                    raise HTTPException(400, "连线将形成循环依赖")
                if current not in seen:
                    seen.add(current)
                    pending.extend(item["to"] for item in edges if item.get("from") == current and item.get("kind", "flow") == "flow")
            edges.append({"from": source, "to": target, "kind": edge_kind})

        if kind == "create_outputs":
            outputs = op.get("outputs", [])
            if not outputs or len(outputs) > 100:
                raise HTTPException(400, "没有可交付的成果")
            source_ids = [key for key in op.get("source_node_ids", []) if key in by_id]
            x, y = point()
            for index, item in enumerate(outputs):
                url = item.get("url", "")
                if not isinstance(url, str) or not url.startswith(("/assets/", "/output/", "/api/storage-files/")):
                    raise HTTPException(400, "交付成果必须是已保存的本地素材")
                image = {key: item[key] for key in ("url", "kind", "width", "height", "name") if key in item}
                node_id = f"agent_{stable}_{index}"
                if node_id in by_id:
                    raise HTTPException(409, "目标节点编号冲突")
                natural_w, natural_h = float(image.get("width") or 0), float(image.get("height") or 0)
                fit = min(520 / natural_w, 440 / natural_h) if natural_w > 0 and natural_h > 0 else 0
                width, height = (max(72, round(natural_w * fit)), max(72, round(natural_h * fit))) if fit else (520, 360)
                node = {"id": node_id, "type": "smart-image", "title": str(op.get("title") or item.get("name") or "Agent 成果")[:120],
                        "x": x, "y": y + index * 500, "w": width, "h": height, "scale": 2, "images": [image], "created_at": int(time.time() * 1000),
                        "runSettings": copy.deepcopy(canvas.get("settings") or {}), "promptDraftHtml": "", "promptDraftText": "",
                        "agentRunId": op.get("run_id"), "agentStepId": op.get("step_id")}
                nodes.append(node)
                by_id[node_id] = node
                for source in source_ids:
                    edge(source, node_id)
        elif kind == "write_prompt":
            text = op.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                raise HTTPException(400, "提示词内容无效")
            if op.get("node_id"):
                node = require([op["node_id"]])[0]
                if node.get("type") != "smart-prompt":
                    raise HTTPException(400, "只能更新提示词节点")
                expected = op.get("expected_fields", {})
                if "text" not in expected or node.get("text") != expected["text"]:
                    raise HTTPException(409, "提示词已被编辑，请选择保留修改或新建节点")
                node["text"] = text
            else:
                x, y = point()
                nodes.append({"id": f"agent_{stable}_prompt", "type": "smart-prompt", "title": str(op.get("title") or "Agent 提示词")[:120],
                              "text": text, "x": x, "y": y, "w": 316, "h": 240, "llmEnabled": False, "promptSplitEnabled": False,
                              "promptSeparator": ";", "created_at": int(time.time() * 1000)})
        elif kind == "connect":
            edge(op.get("from"), op.get("to"), op.get("kind", "flow"))
        elif kind == "group":
            members = require(op.get("node_ids", []))
            if any(node.get("type") == "smart-group" for node in members):
                raise HTTPException(400, "首版分组不支持嵌套分组")
            nodes.append({"id": f"agent_{stable}_group", "type": "smart-group", "title": str(op.get("title") or "Agent 分组")[:120],
                          "x": min(float(n.get("x", 0)) for n in members) - 30, "y": min(float(n.get("y", 0)) for n in members) - 60,
                          "w": 720, "h": 520, "items": list(dict.fromkeys(op["node_ids"])), "images": [], "created_at": int(time.time() * 1000)})
        elif kind == "arrange":
            members = require(op.get("node_ids", []))
            x, y = min(float(n.get("x", 0)) for n in members), min(float(n.get("y", 0)) for n in members)
            columns = 1 if op.get("layout") == "vertical" else min(3, math.ceil(math.sqrt(len(members))))
            for index, node in enumerate(members):
                node["x"], node["y"] = x + index % columns * 380, y + index // columns * 380
        else:
            raise HTTPException(400, "未开放此画布操作")

    def undo(self, canvas_id, operation_id):
        with self.lock:
            canvas = self.read(canvas_id)
            receipt = canvas.get("agent_operation_receipts", {}).get(operation_id)
            if not receipt:
                raise HTTPException(404, "操作不存在")
            if receipt.get("undone"):
                return {"canvas": canvas, "node_ids": receipt["node_ids"], "operation_id": operation_id, "undone": True}
            by_id = {node["id"]: node for node in canvas.get("nodes", [])}
            created = set(receipt["created_ids"])
            for key, after in receipt["after"].items():
                current = by_id.get(key)
                if key in created:
                    if undo_node_state(current) != undo_node_state(after):
                        raise HTTPException(409, "成果节点已被编辑，已保留修改；请手动处理")
                    # New user references must not disappear silently.
                    if any((e.get("from") == key or e.get("to") == key) and e not in receipt["added_edges"] for e in canvas.get("connections", [])):
                        raise HTTPException(409, "成果已有新连线，请先处理连线再撤销")
                    if any(key in n.get("items", []) for n in by_id.values() if n.get("id") not in created):
                        raise HTTPException(409, "成果已加入其他分组，请先移出再撤销")
                elif current is None or any((field in current) != value["exists"] or current.get(field) != value["value"] for field, value in after.items()):
                    raise HTTPException(409, "目标字段已被编辑，撤销未覆盖用户修改")
            for key, before in receipt["before"].items():
                if key in created:
                    canvas.setdefault("agent_deleted_nodes", {})[key] = operation_id
                else:
                    for field, value in before.items():
                        if value["exists"]:
                            by_id[key][field] = copy.deepcopy(value["value"])
                        else:
                            by_id[key].pop(field, None)
            canvas["nodes"] = [node for node in canvas["nodes"] if node["id"] not in created]
            canvas["connections"] = [e for e in canvas.get("connections", []) if e not in receipt["added_edges"]]
            receipt["undone"] = True
            self.save(canvas, expected_revision=canvas.get("revision", 0), _preserve_server=False)
            return {"canvas": canvas, "node_ids": receipt["node_ids"], "operation_id": operation_id, "undone": True}
