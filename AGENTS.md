# 작업자 진입점 — machine-contracts

공장 공통 규칙은 `~/manual/AGENTS.md`가 지정하는 순서를 따른다(`FACTORY_MANUAL.md` →
자기 사이트 장부 → 이 파일). 여기에는 그 규칙을 복제하지 않고, **이 레포에만 해당하는
운영 사실**만 둔다.

## 이 레포가 무엇인가

`factory/machine-observation` 봉투의 정본이다. Chemvas·orca_auto·ollama_bot·Chemleaf가
`machine.json`으로 이 계약을 방출하고, Hermes가 소비한다. 제품 로직은 들어오지 않는다.

## 검증

```bash
make check
```

lint·format·fixture/registry/semantic 스위트를 CI와 같은 순서로 돌린다. 인터프리터는
`jsonschema.Draft202012Validator` import와 `ruff` 실행을 **실제로 시도해** 고르므로
로그인 셸 여부와 무관하다(`PYTHON_BIN`으로 지정 가능). 설치 단계는 없다 — 이 레포는
패키지가 아니고 `.venv`도 두지 않는다.

## 고치기 전에 읽을 것

- [`COMPATIBILITY.md`](COMPATIBILITY.md) — **v1은 동결이다.** 무엇이 additive인지(닫힌
  5항 목록), 무엇이 payload 버전 승격으로 가는지, 릴리스 번호와 pin 전진 규약이 여기 있다.
  `schemas/`·`registry.json`·`scripts/validate.py`·`requirements.txt` 중 하나라도 건드리면
  먼저 읽는다.
- [`README.md`](README.md) — 봉투 9필드와 필수 의미론.
- `.github/pull_request_template.md` — PR은 영향 등급을 하나 선언한다.

## `make check`가 흡수하지 못하는 것

- **소비자 검증은 여기서 못 한다.** 이 레포 CI는 제품을 하나도 돌리지 않는다. 계약 표면을
  바꿨으면 각 제품을 직접 확인하고 그 근거를 PR에 적는다.
- **Hermes 사본은 자동으로 따라오지 않는다.** Windows 스킬이 `registry.json`을 벤더링하고
  validator를 독립 재구현하며, 해시 게이트는 로컬 변조만 잡고 상류 전진은 감지하지 못한다.
  새 표면을 등록한 릴리스는 그 사본을 손으로 갱신해야 끝난다.
- **순서가 있다.** 계약 변경은 여기 먼저 랜딩하고 릴리스를 컷한 뒤, 각 제품이 pin한 SHA를
  자기 PR에서 전진시킨다.
