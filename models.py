# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# DB 인스턴스 생성
db = SQLAlchemy()

# 1. 사용자(User) 테이블
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    credits = db.Column(db.Integer, default=3)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# 2. 기획안(Project) 테이블 (새로 추가된 부분!)
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # 누가 만들었는지 연결
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # 기획안 정보
    title = db.Column(db.String(200), nullable=False)
    platform = db.Column(db.String(50))
    duration = db.Column(db.String(50))
    style = db.Column(db.String(100))
    
    # JSON 데이터를 통째로 텍스트로 저장
    scenes_json = db.Column(db.Text, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# models.py 맨 아래에 추가

class TrialLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50), nullable=False) # IPv6도 고려해서 넉넉하게
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<TrialLog {self.ip_address}>'