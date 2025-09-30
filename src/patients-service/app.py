import os
import uuid
from datetime import date
from typing import Optional

from flask import Flask, request, jsonify
from sqlalchemy import create_engine, Column, String, Date
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker

# ================= Config =================
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@db:5432/emrdb"
)

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


# ================= Model ==================
class Patient(Base):
    __tablename__ = "patients"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    dob = Column(Date)  # YYYY-MM-DD
    phone = Column(String)


# ================= Utils ==================
def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _to_json(p: Patient):
    return {
        "id": str(p.id),
        "name": p.name,
        "dob": p.dob.isoformat() if p.dob else None,
        "phone": p.phone,
    }


# ================= App ====================
app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok", "service": "patients-service"}, 200


@app.route("/patients", methods=["GET", "POST"])
def patients_collection():
    with SessionLocal() as db:
        if request.method == "GET":
            patients = db.query(Patient).order_by(Patient.name.asc()).all()
            return jsonify([_to_json(p) for p in patients]), 200

        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400

        dob = _parse_date(data.get("dob"))
        if data.get("dob") and not dob:
            return jsonify({"error": "bad_dob_format", "hint": "Use YYYY-MM-DD"}), 400

        phone = (data.get("phone") or "").strip() or None

        p = Patient(name=name, dob=dob, phone=phone)
        db.add(p)
        db.commit()
        db.refresh(p)
        return jsonify(_to_json(p)), 201


@app.route("/patients/<uuid:pid>", methods=["GET", "PUT", "DELETE"])
def patients_resource(pid):
    with SessionLocal() as db:
        p = db.query(Patient).get(pid)
        if not p:
            return jsonify({"error": "not_found"}), 404

        if request.method == "GET":
            return jsonify(_to_json(p)), 200

        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name_required"}), 400

            dob = _parse_date(data.get("dob"))
            if data.get("dob") and not dob:
                return jsonify(
                    {"error": "bad_dob_format", "hint": "Use YYYY-MM-DD"}
                ), 400

            phone = (data.get("phone") or "").strip() or None

            p.name = name
            p.dob = dob
            p.phone = phone
            db.commit()
            db.refresh(p)
            return jsonify(_to_json(p)), 200

        db.delete(p)
        db.commit()
        return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7002)
