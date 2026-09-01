# Contributing

Delos Lab은 학습용 코드이므로 기능 수보다 계약의 정확성, 용어의 일관성, 계층 경계와
테스트 가독성을 우선한다. 변경 전 [개념 지도](docs/learning-guide.md),
[아키텍처](docs/architecture.md), [논문-구현 대조표](docs/paper-implementation-map.md)를
읽어 현재 범위를 확인한다.

## 개발 환경

```bash
mise trust
mise run setup
```

`mise.toml`이 Python, Node.js, uv 버전과 작업 진입점을 관리하고, uv가 `uv.lock`에 따라
`.venv`와 Python 의존성을 관리한다. 로컬 전용 설정은 추적되지 않는 `mise.local.toml`에
둔다. `mise trust`는 새 clone에서 프로젝트 설정을 확인한 뒤 최초 한 번만 실행한다.

Python 의존성을 바꾼 경우 `mise run lock`으로 잠금 파일을 갱신하고 `mise run check`를
실행한다. `mise run setup`과 Python 관련 task에는 `--locked`가 적용되므로
`pyproject.toml`과 `uv.lock`이 어긋난 상태는 검증을 통과하지 않는다.

## 변경 원칙

- VirtualLog은 Loglet 설정을 opaque하게 취급한다.
- 프로토콜 패키지는 `runtime`이나 `controller`를 import하지 않는다.
- Controller와 UI는 합의 결과, tail 또는 membership을 결정하지 않는다.
- `replica`, `node`, `process`, `Paxos peer`, `sequencer`, `LogServer`를 서로 바꾸어 쓰지
  않는다.
- 범위 축소나 의도적인 단순화는 숨기지 않고 논문-구현 대조표에 기록한다.
- public API 변경은 구현, 테스트와 문서를 같은 변경에 포함한다.

## 테스트 작성

테스트 이름은 구현 함수보다 검증하는 보장을 설명해야 한다. 분산 시나리오는
“전제 - 사건 - 보장”이 코드에서 읽혀야 한다. 테스트 DSL은 반복되는 setup 동사만 줄이고
assertion이나 production 정책을 숨기지 않는다.

```bash
mise run check
```

전체 검사 전 일부만 확인하려면 `mise run quality`, `mise run test`, `mise run build`를
각각 사용한다. Python 테스트 하나는 다음처럼 선택한다.

```bash
mise run pytest -- tests/unit/native_loglet/test_sequencer.py
```

subprocess 통합 테스트와 E2E는 loopback port를 사용한다. 9400번 포트에서 수동 랩을 실행
중이면 E2E 전에 종료한다.

```bash
mise run setup:e2e
mise run test:e2e
```

## 문서 변경

- 새로운 용어는 처음 등장할 때 소유 컴포넌트와 좌표계를 함께 밝힌다.
- `knownTail`을 fresh `checkTail` 결과나 exact tail로 표현하지 않는다.
- 물리 복사본 관찰을 global commit 또는 hole 판정으로 표현하지 않는다.
- 테스트 개수처럼 쉽게 낡는 숫자보다 검증 명령과 계약을 기록한다.
- 내부 작업 계획, 임시 검토 메모와 도구별 실행 기록은 공개 문서에 포함하지 않는다.

변경을 제출하기 전 [공개 체크리스트](docs/release-checklist.md)의 코드·문서 항목을 다시
확인한다.
