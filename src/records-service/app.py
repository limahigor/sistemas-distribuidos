import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@db:5432/emrdb"
)

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class Record(Base):
    __tablename__ = "records"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    type = Column(String, nullable=False)
    note = Column(String)
    ts = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


def _uuid(s: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(s)
    except Exception:
        return None


def _to_json(r: Record):
    return {
        "id": str(r.id),
        "patientId": str(r.patient_id),
        "type": r.type,
        "note": r.note,
        "ts": r.ts.isoformat(),
    }


app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok", "service": "records-service"}, 200


@app.route("/records", methods=["GET", "POST"])
def records_collection():
    with SessionLocal() as db:
        if request.method == "GET":
            pid = request.args.get("patientId")
            limit = int(request.args.get("limit", "50"))
            q = db.query(Record)
            if pid:
                upid = _uuid(pid)
                if not upid:
                    return jsonify({"error": "bad_patient_id"}), 400
                q = q.filter(Record.patient_id == upid)
            q = q.order_by(Record.ts.desc()).limit(limit)
            items = [_to_json(r) for r in q.all()]
            return jsonify(items), 200

        data = request.get_json(silent=True) or {}
        upid = _uuid(data.get("patientId", ""))
        if not upid:
            return jsonify({"error": "bad_patient_id"}), 400
        rtype = (data.get("type") or "evolucao").strip()
        note = data.get("note")
        rec = Record(patient_id=upid, type=rtype, note=note)
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return jsonify(_to_json(rec)), 201


@app.route("/records/<uuid:rid>", methods=["GET", "PUT", "PATCH", "DELETE"])
def records_resource(rid):
    with SessionLocal() as db:
        rec = db.query(Record).get(rid)
        if not rec:
            return jsonify({"error": "not_found"}), 404

        if request.method == "GET":
            return jsonify(_to_json(rec)), 200

        if request.method in ("PUT", "PATCH"):
            data = request.get_json(silent=True) or {}

            if request.method == "PUT":
                upid = _uuid(data.get("patientId", ""))
                if not upid:
                    return jsonify({"error": "bad_patient_id"}), 400
                rec.patient_id = upid
                rec.type = (data.get("type") or rec.type).strip()
                rec.note = data.get("note")
            else:
                if "patientId" in data:
                    upid = _uuid(data.get("patientId", ""))
                    if not upid:
                        return jsonify({"error": "bad_patient_id"}), 400
                    rec.patient_id = upid
                if "type" in data and data["type"]:
                    rec.type = data["type"].strip() or rec.type
                if "note" in data:
                    rec.note = data["note"]

            db.commit()
            db.refresh(rec)
            return jsonify(_to_json(rec)), 200

        db.delete(rec)
        db.commit()
        return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7003)
