from sqlalchemy import Column, String, BigInteger

from dbsession import Base


class Balance(Base):
    __tablename__ = 'balances'
    transaction_id = Column(BigInteger)
    address = Column(String, primary_key=True)
    amount = Column(BigInteger)
