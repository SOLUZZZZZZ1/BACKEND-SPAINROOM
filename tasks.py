# backend/tasks.py
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import datetime
import os, json, uuid

router = APIRouter()
DATA_DIR = os.getenv("DATA_DIR", "data")
TASKS_PATH = os.path.join(DATA_DIR, "tasks.json")
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(TASKS_PATH):
  with open(TASKS_PATH, "w", encoding="utf-8") as f:
    json.dump([], f)

class Task(BaseModel):
  id: str = Field(default_factory=lambda: uuid.uuid4().hex)
  subject: str
  lead_type: str = Field(default="tenant", description="tenant|owner")
  status: str = Field(default="pending", description="pending|done")
  due_date: str | None = None
  notes: str | None = None
  assignee: str | None = None  # email del franquiciado

def load_tasks():
  with open(TASKS_PATH, "r", encoding="utf-8") as f:
    return json.load(f)

def save_tasks(ts):
  with open(TASKS_PATH, "w", encoding="utf-8") as f:
    json.dump(ts, f, ensure_ascii=False, indent=2)

@router.get("/tasks")
def list_tasks(assignee: str | None = Query(None), status: str | None = Query(None)):
  ts = load_tasks()
  if assignee:
    ts = [t for t in ts if (t.get("assignee") or "").lower() == assignee.lower()]
  if status and status in ("pending", "done"):
    ts = [t for t in ts if t.get("status") == status]
  return ts

@router.post("/tasks")
def create_task(t: Task):
  ts = load_tasks()
  ts.append(t.model_dump())
  save_tasks(ts)
  return t

@router.patch("/tasks/{task_id}")
def update_task(task_id: str, patch: dict):
  ts = load_tasks()
  found = None
  for t in ts:
    if t.get("id") == task_id:
      t.update(patch or {})
      found = t
      break
  if not found:
    raise HTTPException(404, "Task not found")
  save_tasks(ts)
  return found

@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
  ts = load_tasks()
  new_ts = [t for t in ts if t.get("id") != task_id]
  if len(new_ts) == len(ts):
    raise HTTPException(404, "Task not found")
  save_tasks(new_ts)
  return {"ok": True}
