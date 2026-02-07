import os
import json
import time
import requests # 이미지 다운로드용
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, make_response
from google import genai as genai_v2 # 신버전 SDK
from google.genai import types
import replicate
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

# DB 및 로그인
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Project, TrialLog 

load_dotenv()

app = Flask(__name__)

# --- API 키 설정 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ 경고: GEMINI_API_KEY가 없습니다!")

# 1. 텍스트 기획용 (Gemini)
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
        # 마스터 계정 생성 로직 (필요시 유지)
        if not User.query.filter_by(username='master@draftie.app').first():
            new_master = User(username='master@draftie.app', password=generate_password_hash('1234'), credits=999)
            db.session.add(new_master)
            db.session.commit()
    except Exception as e:
        print(f"DB Error: {e}")

# --- [Helper] Replicate (Flux) 이미지 생성 함수 ---
def generate_image_for_scene(scene):
    try:
        # scene 객체에서 image_prompt나 visual_desc를 가져옴
        prompt = scene.get('image_prompt') or scene.get('visual_desc')
        
        if prompt:
            scene_num = scene.get('scene_num', scene.get('scene_number', 0))
            print(f"🎨 이미지 생성 요청 (Flux)... (Scene {scene_num})")
            
            output = replicate.run(
                "black-forest-labs/flux-schnell",
                input={
                    "prompt": prompt,
                    "go_fast": True,
                    "megapixels": "1",
                    "num_outputs": 1,
                    "aspect_ratio": "9:16", # 숏폼 비율로 변경 (1:1 -> 9:16)
                    "output_format": "webp",
                    "output_quality": 80
                }
            )
            image_url_remote = output[0]
            
            # 이미지 다운로드 및 로컬 저장
            filename = f"scene_{int(time.time())}_{scene_num}.webp"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            
            img_data = requests.get(image_url_remote).content
            with open(filepath, 'wb') as f:
                f.write(img_data)
            
            scene['image_url'] = f"/{UPLOAD_FOLDER}/{filename}"
        else:
            scene['image_url'] = None
            
    except Exception as e:
        print(f"❌ 이미지 생성 실패: {e}")
        scene['image_url'] = "https://placehold.co/1080x1920?text=Image+Generation+Failed"
        
    return scene

# --- [CORE] AI 기획안 생성 공통 함수 (Phase 1 적용) ---
def generate_video_script(topic, platform, style="Trendy", duration="Short"):
    """
    로그인 유저와 체험판 유저가 공통으로 사용하는 핵심 함수입니다.
    Phase 1: 마케팅 패키지와 준비물 리스트를 포함한 JSON을 반환합니다.
    """
    print(f"🧠 Gemini 기획 시작: {topic} ({platform})")
    
    system_instruction = f"""
    You are a professional viral video content planner.
    Create a {platform} video plan based on the topic: '{topic}'.
    Style: {style}, Duration: {duration}.
    
    The output must be a valid JSON object with the following structure:
    {{
        "title": "Video Title",
        "opening": "Hooking opening line (0-3s)",
        "scenes": [
            {{
                "scene_number": 1,
                "description": "Visual description for the scene",
                "script": "Voiceover script or text overlay",
                "image_prompt": "A highly detailed, cinematic, photorealistic image description for AI image generation. Describe lighting, camera angle, and subject. English only."
            }},
            ... (3 to 6 scenes)
        ],
        "marketing_title": "A click-bait style, catchy title for YouTube/Instagram upload (Korean)",
        "hashtags": "5-10 relevant hashtags (e.g., #Keyword #Trend)",
        "youtube_desc": "Engaging video description for the upload (2-3 sentences, Korean)",
        "thumbnail_text": "Short, punchy text to be placed on the thumbnail image (Korean)",
        "prep_list": [
            "List of physical items, props, or locations needed for shooting",
            "e.g., White plate, Natural light, Tripod"
        ]
    }}
    
    Requirements:
    1. Language: Korean (except for 'image_prompt' which must be English).
    2. Tone: Trendy, fast-paced, and engaging.
    3. Scenes: Ensure 3 to 6 scenes.
    4. Marketing: The 'marketing_title' and 'thumbnail_text' must be very provocative to induce clicks.
    5. Prep List: Be specific about what to prepare.
    """

    try:
        # 1. Gemini 호출
        response = client_text.models.generate_content(
            model='gemini-2.5-flash',
            contents=system_instruction
        )
        
        # 2. JSON 파싱
        response_text = response.text.replace("```json", "").replace("```", "").strip()
        script_data = json.loads(response_text)
        
        # 3. 이미지 생성 (순차 처리)
        print("🚦 이미지 생성을 시작합니다 (순차 처리 모드)")
        scenes = script_data.get('scenes', [])
        
        for scene in scenes:
            generate_image_for_scene(scene)
            time.sleep(2) # API Rate Limit 방지
            
        return script_data

    except Exception as e:
        print(f"❌ 기획안 생성 중 오류: {e}")
        return None

# --- 라우트 (Routes) ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        # 내가 만든 프로젝트 목록 보여주기
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

# [수정됨] 로그인 유저 생성 라우트 (공통 함수 사용)
@app.route('/generate', methods=['POST'])
@login_required
def generate():
    if current_user.credits <= 0:
        return "<h3>크레딧 부족</h3><a href='/'>뒤로가기</a>"

    platform = request.form.get('platform', 'YouTube Shorts')
    duration = request.form.get('duration', 'Short')
    style = request.form.get('style', 'Trendy')
    product_desc = request.form.get('product_desc')

    # 공통 함수 호출
    script_data = generate_video_script(product_desc, platform, style, duration)

    if script_data:
        # DB 저장 (전체 JSON 저장)
        json_string = json.dumps(script_data, ensure_ascii=False)
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

        flash('기획안 생성 완료! 마케팅 키트가 추가되었습니다. 🚀')
        # scenes 뿐만 아니라 전체 project 데이터를 넘김
        return render_template('result.html', project=script_data, scenes=script_data['scenes'], title=product_desc, user=current_user)
    else:
        return f"<h3>오류 발생: 기획안 생성 실패</h3>"

@app.route('/project/<int:project_id>')
@login_required
def view_project(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id:
        return "권한 없음", 403
    
    script_data = json.loads(project.scenes_json)
    
    # 예전 데이터(리스트 형태)와 호환성 유지
    if isinstance(script_data, list):
        scenes = script_data
        # 가짜 마케팅 데이터라도 만들어서 에러 방지
        script_data = {
            "title": project.title, 
            "scenes": scenes, 
            "marketing_title": "-", 
            "hashtags": "-", 
            "prep_list": []
        }
    
    return render_template('result.html', project=script_data, scenes=script_data['scenes'], title=project.title, user=current_user)

# --- 정적 페이지들 ---
@app.route('/privacy')
def privacy(): return render_template('privacy.html')

@app.route('/terms')
def terms(): return render_template('terms.html')

@app.route('/ads.txt')
def ads_txt(): return app.send_static_file('ads.txt')

@app.route('/robots.txt')
def robots(): return "User-agent: *\nAllow: /", 200, {'Content-Type': 'text/plain'}

@app.route('/guide/shorts')
def guide_shorts(): return render_template('guide_shorts.html')

@app.route('/guide/reels')
def guide_reels(): return render_template('guide_reels.html')

@app.route('/gallery')
def gallery(): return render_template('gallery.html')

# --- [NEW] 비로그인 1회 체험 기능 ---

def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

@app.route('/try', methods=['GET'])
def trial_page():
    if request.cookies.get('trial_used'):
        flash('무료 체험 기회를 이미 사용하셨습니다. 가입 후 무제한으로 이용하세요! 🚀', 'warning')
        return redirect(url_for('signup'))
    return render_template('trial.html')

@app.route('/try/generate', methods=['POST'])
def trial_generate():
    client_ip = get_client_ip()
    existing_log = TrialLog.query.filter_by(ip_address=client_ip).first()
    
    # [배포 시 주석 해제 권장] 이미 사용한 IP 차단
    if existing_log: 
        flash('이미 무료 체험을 완료하신 IP입니다. 회원가입 후 결과를 저장하세요! 💾', 'warning')
        return redirect(url_for('signup'))

    topic = request.form.get('topic')
    platform = request.form.get('platform', 'YouTube Shorts')
    
    if not topic:
        return redirect(url_for('trial_page'))

    # 공통 함수 호출
    script_data = generate_video_script(topic, platform)
        
    if script_data:
        # 사용 기록 저장 (Lock)
        new_log = TrialLog(ip_address=client_ip)
        db.session.add(new_log)
        db.session.commit()
        
        # 결과 페이지 렌더링
        response = make_response(render_template('trial_result.html', project=script_data))
        
        # 쿠키 설정 (1년)
        expires = datetime.now() + timedelta(days=365)
        response.set_cookie('trial_used', 'true', expires=expires)
        
        return response

    flash("AI 서버가 바쁩니다. 잠시 후 다시 시도해주세요.", "danger")
    return redirect(url_for('trial_page'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)