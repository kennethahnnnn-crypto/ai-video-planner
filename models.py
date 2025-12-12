# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# DB 인스턴스 생성 (아직 앱이랑 연결은 안 함)
db = SQLAlchemy()

# 1. 사용자(User) 테이블 설계
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True) # 고유 번호
    username = db.Column(db.String(150), unique=True, nullable=False) # 아이디
    password = db.Column(db.String(200), nullable=False) # 비밀번호 (암호화 예정)
    
    # 크레딧 (무료 사용 횟수) - 가입 시 기본 3회 제공
    credits = db.Column(db.Integer, default=3)
    
    # 가입 날짜
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# 2. (나중에 필요하면) 생성된 기획안 저장 테이블도 여기에 추가 가능