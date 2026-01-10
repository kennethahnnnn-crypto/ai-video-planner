import os
import json
import time
import base64
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# [NEW] Google Gen AI 최신 라이브러리 (v1.0+)
from google import genai
from google.genai import types

# DB 및 로그인
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Project

load_dotenv()

app = Flask(__name__)

# --- [설정] API 키 및 경로 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("❌ 경고: GEMINI_API_KEY가 설정되지 않았습니다!")

# Google Client 초기화 (이거 하나로 텍스트/이미지 다 씀)
client = genai.Client(api_key=GEMINI_API_KEY)

# 이미지 저장 경로 설정 (static 폴더 아래)
UPLOAD_FOLDER = 'static/generated'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# DB 설정
db_url = os.getenv("DATABASE_URL", "sqlite:///database.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-dev-key')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 앱 시작 시 DB 생성
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username='master@draftie.app').first():
            master_pw = generate_password_hash('1234')
            new_master = User(username='master@draftie.app', password=master_pw, credits=9999)
            db.session.add(new_master)
            db.session.commit()
    except Exception as e:
        print(f"⚠️ DB 초기화 오류: {e}")

# --- [핵심] Google Imagen 4 이미지 생성 함수 ---
def generate_image_for_scene(scene):
    try:
        if scene.get('image_prompt'):
            print(f"🎨 이미지 생성 요청 (Imagen 3)... (Scene {scene['scene_num']})")
            
            # Imagen 4 모델 호출
            response = client.models.generate_images(
                model='imagen-4.0-generate-001',
                prompt=scene['image_prompt'],
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="1:1" # 필요하면 "16:9" 등으로 변경 가능
                )
            )
            
            # Google은 URL이 아니라 이미지 데이터(bytes)를 줍니다.
            # 그래서 파일로 저장해야 합니다.
            for generated_image in response.generated_images:
                # 파일명 생성 (유니크하게)
                filename = f"scene_{int(time.time())}_{scene['scene_num']}.png"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                
                # 저장
                with open(filepath, "wb") as f:
                    f.write(generated_image.image.image_bytes)
                
                # 웹에서 접근할 수 있는 경로 저장
                scene['image_url'] = f"/{UPLOAD_FOLDER}/{filename}"
                
        else:
            scene['image_url'] = None
            
    except Exception as e:
        print(f"❌ 이미지 생성 실패 (Scene {scene.get('scene_num')}): {e}")
        # 에러 시 기본 이미지
        scene['image_url'] = "https://placehold.co/1024x1024?text=Image+Error"
        
    return scene

# ================= 라우트 정의 =================

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
            flash('아이디/비번 확인 필요')
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
        return "<h3>크레딧 부족!</h3><a href='/'>뒤로가기</a>"

    # 입력 받기
    platform = request.form.get('platform')
    duration = request.form.get('duration')
    style = request.form.get('style')
    product_desc = request.form.get('product_desc')

    # 프롬프트 구성
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
            "image_prompt": "High quality image generation prompt for Imagen 3, describing this scene visually, style is {style}, english"
        }}
    ]
    """

    try:
        # 1. Gemini 2.5 Flash로 기획안 생성 (텍스트)
        # 새 SDK 문법 적용
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        text_result = response.text.replace("```json", "").replace("```", "").strip()
        scenes = json.loads(text_result)
        
        # 2. Imagen 3로 이미지 병렬 생성
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(generate_image_for_scene, scenes))

        # 3. DB 저장
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

        flash('기획안 생성 및 저장 완료!')
        return render_template('result.html', scenes=scenes, title=product_desc, user=current_user)

    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return f"<h3>오류가 발생했습니다.</h3><p>{e}</p>"

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
        # 1. 기존 마스터 계정이 있으면 삭제
        existing_master = User.query.filter_by(username='master@draftie.app').first()
        if existing_master:
            db.session.delete(existing_master)
            db.session.commit()
        
        # 2. 마스터 계정 새로 생성 (비번: 1234)
        # pbkdf2 방식은 안전하면서 호환성이 좋습니다.
        master_pw = generate_password_hash('1234', method='pbkdf2:sha256')
        new_master = User(username='master@draftie.app', password=master_pw, credits=9999)
        
        db.session.add(new_master)
        db.session.commit()
        
        return "✅ 마스터 계정 복구 완료! <br>ID: master@draftie.app <br>PW: 1234 <br><a href='/login'>로그인하러 가기</a>"
        
    except Exception as e:
        return f"❌ 오류 발생: {e}"



if __name__ == '__main__':
    app.run(debug=True, port=5001)