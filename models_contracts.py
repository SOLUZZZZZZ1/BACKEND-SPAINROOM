# models_contracts.py
from datetime import datetime
import random
from app import db

def gen_ref():
    return f"SR-{random.randint(0, 99999):05d}"

class Contract(db.Model):
    __tablename__ = "contracts"
    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    ref           = db.Column(db.String(16), unique=True, nullable=False, index=True)  # SR-12345
    owner_id      = db.Column(db.String(64))    # tel/email propietario
    tenant_id     = db.Column(db.String(64))    # tel/email inquilino
    franchisee_id = db.Column(db.String(64))    # tel/email franquiciado
    status        = db.Column(db.String(32), default="draft")  # draft|signed|active|closed
    meta_json     = db.Column(db.JSON)

    items         = db.relationship("ContractItem", backref="contract", cascade="all, delete-orphan")

    @staticmethod
    def new_ref():
        while True:
            r = gen_ref()
            if not Contract.query.filter_by(ref=r).first():
                return r

class ContractItem(db.Model):
    """
    Línea de contrato por habitación.
    - sub_ref: SR-12345-01 (único por contrato)
    - room_id: vínculo a Room
    - owner_id, franchisee_id: identificadores lógicos (tel/email/usuario)
    - split_owner, split_franchisee: % de reparto (0..1) referencial
    """
    __tablename__ = "contract_items"
    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    contract_id   = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=False)
    sub_ref       = db.Column(db.String(24), nullable=False, index=True)  # SR-12345-01
    room_id       = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)

    owner_id      = db.Column(db.String(64))
    franchisee_id = db.Column(db.String(64))
    status        = db.Column(db.String(32), default="draft")  # draft|ready|published|closed

    split_owner       = db.Column(db.Float, default=0.80)  # 80% (ejemplo)
    split_franchisee  = db.Column(db.Float, default=0.20)  # 20%

    meta_json     = db.Column(db.JSON)

    __table_args__ = (db.UniqueConstraint("contract_id", "sub_ref", name="uq_contract_subref"),)

    @staticmethod
    def make_sub_ref(contract_ref: str, index_1based: int) -> str:
        return f"{contract_ref}-{index_1based:02d}"
