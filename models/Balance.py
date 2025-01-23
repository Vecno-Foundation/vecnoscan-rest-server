from sqlalchemy import Column, String, BigInteger

from dbsession import Base


class Balance(Base):
    __tablename__ = 'balances'
    transaction_id = Column(BigInteger, primary_key=True)
    address = Column(String)
    amount = Column(BigInteger)
