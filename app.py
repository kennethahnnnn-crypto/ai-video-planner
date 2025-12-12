import os
import json
import time
from flask import Flask, render_template, request, redirect, url_for, flash
import google.generativeai as genai
from openai import OpenAI
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# DB 및 로그인 관련 라이브러리
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash # 암호화 도구
from models import db, User

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

# 보안 및 DB 설정
app.config['SECRET_KEY'] = 'my_super_secret_key_draftie_2025'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 앱 시작 시 DB 생성 및 마스터 계정 자동 생성
with app.app_context():
    db.create_all()
    
    # 마스터 계정이 이미 있는지 확인
    master_user = User.query.filter_by(username='master@draftie.app').first()
    
    if not master_user:
        # 없으면 새로 생성 (비밀번호 1234, 크레딧 999개)
        master_pw = generate_password_hash('1234')
        new_master = User(username='master@draftie.app', password=master_pw, credits=999)
        
        db.session.add(new_master)
        db.session.commit()
        print("👑 마스터 계정이 생성되었습니다: master@draftie.app / 1234")

# --- 이미지 병렬 생성 함수 ---
def generate_image_for_scene(scene):
    try:
        if scene.get('image_prompt'):
            print(f"🎨 이미지 생성 요청... (Scene {scene['scene_num']})")
            response = client.images.generate(
                model="dall-e-3",
                prompt=scene['image_prompt'],
                size="1024x1024",
                quality="standard",
                n=1
            )
            scene['image_url'] = response.data[0].url
        else:
            scene['image_url'] = None
    except Exception as e:
        print(f"❌ 실패 (Scene {scene['scene_num']}): {e}")
        scene['image_url'] = None
    return scene

# ================= 라우트(페이지) 정의 =================

@app.route('/')
def index():
    # 로그인이 되어 있다면 -> 바로 기획 도구(index.html) 화면으로
    if current_user.is_authenticated:
        return render_template('index.html', user=current_user)
    
    # 로그인이 안 되어 있다면 -> 랜딩 페이지(landing.html) 보여주기
    else:
        return render_template('landing.html')

# --- 회원가입 ---
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # 중복 아이디 체크
        user = User.query.filter_by(username=username).first()
        if user:
            flash('이미 존재하는 아이디입니다.')
            return redirect(url_for('signup'))

        # 비밀번호 암호화 후 저장
# method 옵션을 지우면 알아서 가장 안전한 기본값(pbkdf2)을 씁니다.
        new_user = User(username=username, password=generate_password_hash(password))
        db.session.add(new_user)
        db.session.commit()
        
        flash('가입 완료! 로그인해주세요.')
        return redirect(url_for('login'))
    
    return render_template('signup.html')

# --- 로그인 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        # 아이디 확인 및 비밀번호 대조
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('아이디 또는 비밀번호가 틀렸습니다.')
            
    return render_template('login.html')

# --- 로그아웃 ---
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- 생성 기능 (로그인한 사람만 가능) ---
@app.route('/generate', methods=['POST'])
@login_required # 핵심: 로그인 안 하면 못 씀!
def generate():
    # 크레딧 체크 (0개면 생성 불가)
    if current_user.credits <= 0:
        return "<h3>크레딧이 부족합니다! (충전 기능은 준비 중)</h3><a href='/'>돌아가기</a>"

    start_time = time.time()
    platform = request.form.get('platform')
    duration = request.form.get('duration')
    style = request.form.get('style')
    product_desc = request.form.get('product_desc')

    prompt = f"""
    당신은 전문 영상 광고 디렉터입니다.
    [요청사항]
    - 플랫폼: {platform}
    - 영상 길이: {duration}
    - 영상 스타일: {style}
    - 제품: {product_desc}

    [출력 조건]
    JSON 형식으로만 답하세요.
    [
        {{
            "scene_num": 1,
            "time": "0-3초",
            "script": "대사",
            "visual_desc": "화면 설명",
            "image_prompt": "High quality image generation prompt for DALL-E 3, describing this scene visually, style is {style}, english"
        }}
    ]
    """

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        text_result = response.text.replace("```json", "").replace("```", "").strip()
        scenes = json.loads(text_result)
        
        # 이미지 생성 (병렬)
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(generate_image_for_scene, scenes))

        # 크레딧 1 차감 및 저장
        current_user.credits -= 1
        db.session.commit()

        return render_template('result.html', scenes=scenes, title=product_desc, user=current_user)

    except Exception as e:
        print(f"에러: {e}")
        return f"오류 발생: {e}"

if __name__ == '__main__':
    app.run(debug=True, port=5001)