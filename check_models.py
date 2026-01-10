import os
from google import genai
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ API Key가 없습니다. .env 파일을 확인해주세요.")
else:
    print(f"🔑 API Key 확인됨: {api_key[:5]}...")
    print("📡 사용 가능한 모델 목록 조회 중...\n")

    try:
        client = genai.Client(api_key=api_key)
        
        # 모델 목록 가져오기
        model_list = list(client.models.list())
        
        found_imagen = False
        print("--- [ 모델 리스트 ] ---")
        for m in model_list:
            # 모델 이름 출력
            print(f"• {m.name}")
            
            # Imagen 모델인지 확인
            if "imagen" in m.name.lower():
                found_imagen = True
        
        print("\n-----------------------")
        if found_imagen:
            print("✅ 'imagen' 모델이 발견되었습니다! 위 이름을 복사해서 사용하세요.")
        else:
            print("❌ 목록에 'imagen' 모델이 없습니다.")
            print("   (현재 계정/API Key로는 구글 이미지 생성 모델 권한이 없습니다.)")
            
    except Exception as e:
        print(f"❌ 조회 실패: {e}")