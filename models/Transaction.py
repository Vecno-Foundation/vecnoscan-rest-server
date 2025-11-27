# models/Transaction.py
from sqlalchemy import Column, Integer, BigInteger, SmallInteger

from dbsession import Base
from helper.PublicKeyType import get_public_key_type
from models.AddressColumn import AddressColumn
from models.type_decorators.HexColumn import HexColumn


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id = Column(HexColumn, primary_key=True)
    subnetwork_id = Column(SmallInteger)
    hash = Column(HexColumn)
    mass = Column(Integer)
    payload = Column(HexColumn)
    block_time = Column(BigInteger)


class TransactionOutput(Base):
    __tablename__ = "transactions_outputs"

    transaction_id = Column(HexColumn, primary_key=True)
    index = Column(SmallInteger, primary_key=True)
    amount = Column(BigInteger)
    script_public_key = Column(HexColumn)
    _script_public_key_address = Column("script_public_key_address", AddressColumn)

    @property
    def script_public_key_type(self):
        return get_public_key_type(self.script_public_key)

    @property
    def script_public_key_address(self):
        """Expose the private column as a public property (used in serialization)"""
        return self._script_public_key_address

    # Optional: make it writable if needed
    @script_public_key_address.setter
    def script_public_key_address(self, value):
        self._script_public_key_address = value


class TransactionInput(Base):
    __tablename__ = "transactions_inputs"

    transaction_id = Column(HexColumn, primary_key=True)
    index = Column(SmallInteger, primary_key=True)
    previous_outpoint_hash = Column(HexColumn)
    previous_outpoint_index = Column(SmallInteger)
    signature_script = Column(HexColumn)
    sig_op_count = Column(SmallInteger)
    previous_outpoint_script = Column(HexColumn)
    previous_outpoint_amount = Column(BigInteger)