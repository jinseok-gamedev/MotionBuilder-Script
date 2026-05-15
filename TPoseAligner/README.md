# TPoseAligner

MotionBuilder 에서 두 HumanIK Character (소스 / 타겟) 의 T-Pose stance 를 자동으로 정렬해서 리타겟 품질을 끌어올리는 도구입니다. 실제 스켈레톤을 회전시키지 않고 `FBCharacter.SetROffset` 만 사용하므로 **비파괴**이며, 마음에 들지 않으면 한 클릭으로 원상복구됩니다.

## 무엇을 해결하나

| 문제 | 원인 | TPoseAligner 의 대응 |
|---|---|---|
| 어깨가 솟구치거나 처짐 | 클래비클 stance 가 어긋남 | 어깨 체인을 따로 정렬하고 30° 초과 오프셋이면 경고 |
| 손목이 180° 플립 | gimbal / twist 본 미스매치 | 같은 결과인 ±180° 후보 중 작은 회전 선택 |
| 토우가 위로 들림 | 발 stance 가 정확히 수평이 아님 | 토우 본 위치로 forward 벡터 자동 보정 |
| 무릎/팔꿈치 IK 과신전 | T-Pose 가 완전한 직선 | ~1° 미세 굽힘을 보존해 IK singular 회피 |
| Y-up 이 아닌 씬 | HumanIK 솔버는 Y-up 가정 | 시작 시 검사하고 진행 차단 |
| 큰 스케일 차이 | 비례 불일치 (#1 원인) | proportion 비교 후 Match Source 추천 |

## 요구사항

- MotionBuilder 2018 이상 (PySide2) 또는 2023 이상 (PySide6) 권장
- 소스/타겟 캐릭터가 모두 **이미 Characterize 완료** 되어 있어야 함 (Pair 모드)
- Y-up 씬 (Scene Settings 에서 확인)

## 설치

### 옵션 A: 메뉴 자동 등록 (권장)

1. `TPoseAligner/` 폴더 전체를 다음 위치로 복사:
   - Windows: `Documents\MB\<버전>\config\Scripts\Startup\`
2. MotionBuilder 재시작
3. 상단 메뉴에 **"TPose Align"** 항목이 추가됨

### 옵션 B: 수동 실행 (개발자용)

```python
import sys
sys.path.insert(0, r"C:\path\to\MotionBuilder-Script")

from TPoseAligner.ui import show_align_dialog
show_align_dialog()
```

## 사용 방법

### 단발성 리타겟 (Pair Align)

1. 소스 / 타겟 FBX 두 개를 모두 임포트 (둘 다 Characterize 완료 상태)
2. 메뉴 → **TPose Align → T-Pose Pair Align...**
3. 다이얼로그에서 소스 / 타겟 캐릭터 선택
4. 정렬할 체인 체크 (기본은 Spine + 팔 + 다리, 손가락은 OFF)
5. **Align Both** 클릭
6. Diff 패널에서 색상 확인:
   - 녹색: 10° 이하 (정상)
   - 황색: 10°~30° (확인 필요)
   - 적색: 30° 초과 (해당 본 매핑이 잘못됐을 가능성, 더블클릭으로 본 선택)
7. 만족하면 **Connect & Activate** → MotionBuilder 의 표준 Bake → Plot To Skeleton 흐름 진행
8. 다음에 같은 페어 작업할 때 빠르게 재현하려면 **Save current as preset**

### 대량 리타겟 (Batch Retarget)

1. 메뉴 → **TPose Align → Batch Retarget...**
2. 타겟 FBX, 소스 모션 폴더, 출력 폴더 지정
3. **Run batch** → 폴더 내 모든 .fbx 가 자동으로 처리됨
4. 진행 표 에서 파일별 성공/실패 확인

소스가 이미 Characterize 되어 있으면 그대로 사용하고, 안 되어 있으면 MotionBuilder 또는 3dsMax Biped 본 이름 규칙으로 자동 Characterize 를 시도합니다.

## 디렉토리 구조

```
TPoseAligner/
├── README.md
├── install_menu.py             # MotionBuilder 메뉴 등록
├── core/
│   ├── tpose_align.py          # 핵심 정렬 로직
│   ├── canonical_pose.py       # 본별 캐노니컬 방향 테이블
│   ├── chain_groups.py         # 체인 정의
│   ├── math_utils.py           # 행렬/쿼터니언 헬퍼 (numpy 의존성 없음)
│   ├── snapshot.py             # 오프셋 스냅샷/복원
│   ├── validation.py           # Y-up, 오프셋 등급화, proportion
│   └── preset_io.py            # JSON preset 저장/불러오기
├── batch/
│   └── batch_retarget.py       # 폴더 단위 자동 처리
├── ui/
│   ├── align_dialog.py         # Pair Align 다이얼로그
│   └── batch_dialog.py         # Batch 다이얼로그
└── presets/                    # 사용자 preset (.json) 저장
```

## 동작 원리 요약

1. `validation.assert_y_up_scene()` - Y-up 검사
2. `snapshot.capture(character)` - 현재 오프셋 스냅샷 (복원용)
3. (옵션) `reset_all_offsets(character)` - 깨끗한 기준선
4. `character.GoToStancePose()` - 현재 stance 로 이동
5. 체인 단위 루프 (부모 → 자식 순서로):
   - `bone = character.GetModel(node_id)` 로 실제 본 획득
   - 본의 현재 월드 회전 계산
   - `canonical_pose.py` 의 캐노니컬 방향 조회
   - `R_offset = canonical_quat * inverse(current_quat)` 계산
   - wrist flip guard 로 ±180° 중 작은 쪽 선택
   - `character.SetROffset(node_id, FBRVector(rx, ry, rz))` 적용
6. 손바닥/발바닥 후처리, twist 본 부모-동기 처리
7. 모든 적용 오프셋 등급화 → 황/적이면 `warnings` 로 보고

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| "Scene is not Y-up" 에러 | File → Scene Settings → Up Axis 를 Y 로 변경 |
| 정렬 후 캐릭터가 뒤집힘 | wrist flip guard 가 켜진 상태에서 다시 시도 |
| 어떤 본이 적색 (>30°) | Diff 표에서 본 더블클릭 후 시각적으로 확인. 매핑 잘못됐으면 Characterization 재확인 |
| 정렬 후 손가락이 어색 | `Include fingers` 옵션 OFF 후 재시도 (대부분 손가락은 원본이 더 자연스러움) |
| 결과가 마음에 안 듦 | Restore Snapshot 한 번이면 정렬 전으로 돌아감 |
| Batch 모드에서 자동 Characterize 실패 | 본 이름 규칙이 다른 경우 - 소스 FBX 들을 먼저 수동 Characterize 후 다시 batch 실행 |

## 검증 시나리오

| 시나리오 | 기대 결과 |
|---|---|
| 캐노니컬 T-Pose 에 가까운 캐릭터 | 모든 오프셋 < 5° (전부 녹색) |
| A-Pose (어깨 45° 내림) 캐릭터 | UpArm 에 약 45° 오프셋 (황색), 정렬 후 정확한 T-Pose 시각 확인 |
| 손목이 90° 돌아간 캐릭터 | wrist flip guard 로 90° 적용 (180° 안 가는지 확인) |
| 발이 살짝 회전된 캐릭터 | feet flat / forward 후처리로 정면 향함 |
| 같은 mocap 데이터를 두 다른 캐릭터에 리타겟 | 정렬 전 vs 후 어깨 솟구침/팔 꼬임 비교 |
| Y-up 이 아닌 씬 | 다이얼로그에 빨간 경고 표시 + Align 버튼 무시 |

## 산업 사례 / 참고

- [eksod/Retargeter](https://github.com/eksod/Retargeter) - 본 도구의 batch 모듈이 차용한 클래식 패턴 (폴더 단위 일괄 처리)
- [Neill3d/OpenMoBu](https://github.com/Neill3d/OpenMoBu) - MotionBuilder 확장 전반에 대한 좋은 레퍼런스
- Unreal IK Retargeter (5.4+) 의 `auto_align_all_bones()` - 본 도구의 per-chain 토글이 영감을 받은 부분
- Mocappys 의 ["Mapping File" 패턴](https://mocappys.com/retargeting-animation-motionbuilder/) - 본 도구의 preset 저장/불러오기가 자동화한 부분

## 비파괴성에 대한 노트

본 도구가 건드리는 것은 오직 `FBCharacter.SetROffset` (그리고 옵션으로 `SetTOffset`) 뿐입니다. 실제 스켈레톤의 본 회전, 메시, 스킨 weight, 컨트롤 릭은 일절 변경되지 않습니다. 따라서:

- 정렬 결과가 마음에 안 들면 `Restore Snapshot` 또는 메뉴의 `Reset Offsets` 한 번으로 완전히 복구됩니다.
- 오프셋은 캐릭터 노드 자체에 저장되므로 .fbx 를 저장하면 다음에 열었을 때도 유지됩니다.
- 재-Characterize 가 필요 없습니다.

## 라이선스

이 코드는 사내 프로덕션 도구로 작성되었으며 자유롭게 수정/배포해도 좋습니다.
