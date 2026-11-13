from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.inbox import writer
from app.core.db import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/inbox")
def report(start: date, end: date, user=Depends(writer), db: Session = Depends(get_db)):
    if end < start or (end - start).days > 365:
        raise HTTPException(400, "Выберите период не больше года")
    zone = ZoneInfo("Asia/Seoul")
    params = {
        "start": datetime.combine(start, time(), zone),
        "end": datetime.combine(end + timedelta(days=1), time(), zone),
        "owner": None if user.role == "admin" else user.id,
    }
    # Each unanswered sequence is matched to its first successfully sent reply.
    # Notes, system events and failed/queued sends cannot stop the response timer.
    rows = (
        db.execute(
            text("""
        WITH incoming AS (
          SELECT m.id, m.conversation_id, m.created_at, c.assignee_id
          FROM messages m JOIN conversations c ON c.id=m.conversation_id
          WHERE m.direction='in' AND NOT m.is_private_note AND m.author_type!='system'
            AND m.created_at>=:start AND m.created_at<:end
        ), matched AS (
          SELECT i.*, r.id reply_id, r.author_id, r.author_type, r.reply_at
          FROM incoming i LEFT JOIN LATERAL (
            SELECT m.id, m.author_id, m.author_type, COALESCE(m.sent_at,m.created_at) reply_at
            FROM messages m WHERE m.conversation_id=i.conversation_id
              AND m.direction='out' AND NOT m.is_private_note AND m.author_type!='system'
              AND m.status IN ('sent','delivered','read')
              AND COALESCE(m.sent_at,m.created_at)>=i.created_at
            ORDER BY COALESCE(m.sent_at,m.created_at),m.id LIMIT 1
          ) r ON true
        ), episodes AS (
          SELECT reply_id, author_id, author_type, reply_at, min(created_at) first_at
          FROM matched WHERE reply_id IS NOT NULL GROUP BY 1,2,3,4
        ), counts AS (
          SELECT COALESCE(assignee_id,'unassigned') owner, count(*) inbound,
            count(*) FILTER (WHERE reply_id IS NULL) unanswered
          FROM matched GROUP BY 1
        ), responses AS (
          SELECT CASE WHEN author_type='ai' THEN 'ai'
            ELSE COALESCE(author_id,'phone') END owner, count(*) replies,
            avg(extract(epoch FROM reply_at-first_at)) average_seconds,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM reply_at-first_at)) median_seconds
          FROM episodes GROUP BY 1
        )
        SELECT COALESCE(c.owner,r.owner) owner, u.email, COALESCE(c.inbound,0) inbound,
          COALESCE(c.unanswered,0) unanswered, COALESCE(r.replies,0) replies,
          r.average_seconds, r.median_seconds
        FROM counts c FULL JOIN responses r USING(owner)
        LEFT JOIN users u ON u.id=COALESCE(c.owner,r.owner)
        WHERE CAST(:owner AS TEXT) IS NULL OR COALESCE(c.owner,r.owner)=:owner
        ORDER BY inbound DESC, owner
    """),
            params,
        )
        .mappings()
        .all()
    )
    return {"start": start, "end": end, "timezone": "Asia/Seoul", "rows": [dict(r) for r in rows]}
