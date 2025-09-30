import os
import uuid
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@db:5432/emrdb"
)

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    doctor_id = Column(String, nullable=False)
    when_ts = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="scheduled")


def _uuid(s: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(s)
    except Exception:
        return None


def _parse_when(s: str) -> Optional[datetime]:
    """
    Aceita ISO 8601 como '2025-09-22T14:30:00Z' ou com offset +00:00.
    """
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _to_json(a: Appointment):
    return {
        "id": str(a.id),
        "patientId": str(a.patient_id),
        "doctorId": a.doctor_id,
        "when": a.when_ts.isoformat(),
        "status": a.status,
    }


app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok", "service": "scheduling-service"}, 200


@app.route("/appointments", methods=["GET", "POST"])
def appointments_collection():
    with SessionLocal() as db:
        if request.method == "GET":
            pid = request.args.get("patientId")
            q = db.query(Appointment)
            if pid:
                upid = _uuid(pid)
                if not upid:
                    return jsonify({"error": "bad_patient_id"}), 400
                q = q.filter(Appointment.patient_id == upid)
            items = [_to_json(a) for a in q.order_by(Appointment.when_ts.asc()).all()]
            return jsonify(items), 200

        # POST
        data = request.get_json(silent=True) or {}
        upid = _uuid(data.get("patientId", ""))
        if not upid:
            return jsonify({"error": "bad_patient_id"}), 400

        doctor_id = (data.get("doctorId") or "").strip()
        if not doctor_id:
            return jsonify({"error": "doctor_id_required"}), 400

        when_raw = data.get("when")
        when_ts = _parse_when(when_raw) if when_raw else None
        if not when_ts:
            return jsonify(
                {
                    "error": "bad_when",
                    "hint": "Use ISO 8601 (ex.: 2025-09-22T14:30:00Z)",
                }
            ), 400

        status = (data.get("status") or "scheduled").strip() or "scheduled"
        a = Appointment(
            patient_id=upid, doctor_id=doctor_id, when_ts=when_ts, status=status
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return jsonify(_to_json(a)), 201


@app.route("/appointments/<uuid:aid>", methods=["GET", "PUT", "DELETE"])
def appointments_resource(aid):
    with SessionLocal() as db:
        a = db.query(Appointment).get(aid)
        if not a:
            return jsonify({"error": "not_found"}), 404

        if request.method == "GET":
            return jsonify(_to_json(a)), 200

        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            if "patientId" in data:
                upid = _uuid(data.get("patientId", ""))
                if not upid:
                    return jsonify({"error": "bad_patient_id"}), 400
                a.patient_id = upid
            if "doctorId" in data:
                doctor_id = (data.get("doctorId") or "").strip()
                if not doctor_id:
                    return jsonify({"error": "doctor_id_required"}), 400
                a.doctor_id = doctor_id
            if "when" in data:
                w = _parse_when(data.get("when") or "")
                if not w:
                    return jsonify({"error": "bad_when", "hint": "Use ISO 8601"}), 400
                a.when_ts = w
            if "status" in data and (data["status"] or "").strip():
                a.status = data["status"].strip()
            db.commit()
            db.refresh(a)
            return jsonify(_to_json(a)), 200

        db.delete(a)
        db.commit()
        return "", 204


@app.put("/appointments/<uuid:aid>/cancel")
def cancel(aid):
    with SessionLocal() as db:
        a = db.query(Appointment).get(aid)
        if not a:
            return jsonify({"error": "not_found"}), 404
        a.status = "canceled"
        db.commit()
        db.refresh(a)
        return jsonify(_to_json(a)), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7004)
