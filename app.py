import os
import json
import time
import requests # 이미지 다운로드용
from flask import Flask, render_template, request, redirect, url_for, flash
# import google.generativeai as genai  <-- 삭제함 (더 이상 안 씀)
from google import genai as genai_v2 # 신버전 SDK (이것만 씀)
from google.genai import types
import replicate # [NEW] Replicate 추가
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# DB 및 로그인
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Project

load_dotenv()

app = Flask(__name__)

# --- API 키 설정 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Render 환경변수에 REPLICATE_API_TOKEN 추가 필수!

if not GEMINI_API_KEY:
    print("❌ 경고: GEMINI_API_KEY가 없습니다!")

# 1. 텍스트 기획용 (Gemini) - Google Client
client_text = genai_v2.Client(api_key=GEMINI_API_KEY)

# 이미지 저장 경로
UPLOAD_FOLDER = 'static/generated'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# DB 설정
db_url = os.getenv("DATABASE_URL", "sqlite:///database.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-key')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 앱 시작 시 DB 초기화
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username='master@draftie.app').first():
            new_master = User(username='master@draftie.app', password=generate_password_hash('1234'), credits=999)
            db.session.add(new_master)
            db.session.commit()
    except Exception as e:
        print(f"DB Error: {e}")

# --- [핵심] Replicate (Flux) 이미지 생성 함수 ---
def generate_image_for_scene(scene):
    try:
        if scene.get('image_prompt'):
            print(f"🎨 이미지 생성 요청 (Flux)... (Scene {scene['scene_num']})")
            
            output = replicate.run(
                "black-forest-labs/flux-schnell",
                input={
                    "prompt": scene['image_prompt'],
                    "go_fast": True,
                    "megapixels": "1",
                    "num_outputs": 1,
                    "aspect_ratio": "1:1",
                    "output_format": "webp",
                    "output_quality": 80
                }
            )
            image_url_remote = output[0]
            
            filename = f"scene_{int(time.time())}_{scene['scene_num']}.webp"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            
            img_data = requests.get(image_url_remote).content
            with open(filepath, 'wb') as f:
                f.write(img_data)
            
            scene['image_url'] = f"/{UPLOAD_FOLDER}/{filename}"
        else:
            scene['image_url'] = None
            
    except Exception as e:
        print(f"❌ 이미지 생성 실패 (Scene {scene.get('scene_num')}): {e}")
        scene['image_url'] = "https://placehold.co/1024x1024?text=Image+Generation+Failed"
        
    return scene

@app.route('/')
def index():
    if current_user.is_authenticated:
        my_projects = Project.query.filter_by(user_id=current_user.id).order_by(Project.created_at.desc()).all()
        return render_template('index.html', user=current_user, projects=my_projects)
    else:
        return render_template('landing.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('이미 존재하는 아이디입니다.')
            return redirect(url_for('signup'))
        new_user = User(username=username, password=generate_password_hash(password))
        db.session.add(new_user)
        db.session.commit()
        flash('가입 완료!')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('아이디/비번 확인')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/generate', methods=['POST'])
@login_required
def generate():
    if current_user.credits <= 0:
        return "<h3>크레딧 부족</h3><a href='/'>뒤로가기</a>"

    platform = request.form.get('platform')
    duration = request.form.get('duration')
    style = request.form.get('style')
    product_desc = request.form.get('product_desc')

    prompt = f"""
    당신은 전문 영상 광고 디렉터입니다.
    [요청사항]
    - 플랫폼: {platform} / 길이: {duration} / 스타일: {style} / 제품: {product_desc}

    [출력 조건]
    JSON 형식으로만 답하세요.
    [
        {{
            "scene_num": 1,
            "time": "0-3초",
            "script": "대사",
            "visual_desc": "화면 설명",
            "image_prompt": "High quality image generation prompt for realistic style, describing this scene visually, style is {style}, english"
        }}
    ]
    """

    try:
        # 1. 텍스트 기획 (Gemini)
        response = client_text.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        text_result = response.text.replace("```json", "").replace("```", "").strip()
        scenes = json.loads(text_result)
        
        # 2. 이미지 생성 (수정된 부분: 순차 처리 + 대기 시간)
        # Replicate 잔액 이슈($5/$10 구간)를 피하기 위해 한 장씩 천천히 만듭니다.
        print("🚦 이미지 생성을 시작합니다 (순차 처리 모드)")
        
        for scene in scenes:
            generate_image_for_scene(scene)
            # 중요: API가 숨 쉴 시간을 줍니다. (2초 대기)
            # 만약 또 429 에러가 나면 이 숫자를 5로 늘려주세요.
            time.sleep(5) 

        # 3. 저장 (기존과 동일)
        json_string = json.dumps(scenes, ensure_ascii=False)
        new_project = Project(
            user_id=current_user.id,
            title=product_desc[:30],
            platform=platform,
            duration=duration,
            style=style,
            scenes_json=json_string
        )
        current_user.credits -= 1
        db.session.add(new_project)
        db.session.commit()

        flash('기획안 생성 완료!')
        return render_template('result.html', scenes=scenes, title=product_desc, user=current_user)

    except Exception as e:
        print(f"❌ 에러: {e}")
        return f"<h3>오류 발생: {e}</h3>"

@app.route('/project/<int:project_id>')
@login_required
def view_project(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id:
        return "권한 없음", 403
    scenes = json.loads(project.scenes_json)
    return render_template('result.html', scenes=scenes, title=project.title, user=current_user)

@app.route('/fix-master')
def fix_master():
    try:
        existing = User.query.filter_by(username='master@draftie.app').first()
        if existing: db.session.delete(existing)
        
        new_master = User(username='master@draftie.app', password=generate_password_hash('1234'), credits=999)
        db.session.add(new_master)
        db.session.commit()
        return "마스터 계정 리셋 완료"
    except Exception as e:
        return f"에러: {e}"
    
# app.py 하단 라우트 부분

# ... (기존 코드들) ...

# [추가할 부분] 법적 페이지 및 ads.txt 연결
@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/ads.txt')
def ads_txt():
    return app.send_static_file('ads.txt')

@app.route('/robots.txt')
def robots():
    return "User-agent: *\nAllow: /", 200, {'Content-Type': 'text/plain'}

# if __name__ == '__main__':  <-- 이 줄 위에 넣으세요!
#     app.run(...)

if __name__ == '__main__':
    app.run(debug=True, port=5001)