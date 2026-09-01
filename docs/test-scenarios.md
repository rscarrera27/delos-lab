# 테스트로 읽는 Delos Lab

이 저장소의 테스트는 구현 세부사항을 모두 같은 수준으로 읽는 목록이 아니다. 처음 보는
학생은 아래 순서로 “전제 - 사건 - 보장”을 따라가면 된다. 각 테스트 이름은 실패 원인이
아니라 확인하려는 분산 시스템 계약을 말한다.

## 1. NativeLoglet 데이터 경로

먼저 `tests/unit/native_loglet/test_sequencer.py`를 읽는다.

| 시나리오 | 전제와 사건 | 확인하는 보장 |
|---|---|---|
| 순차 append와 재시도 | 같은 command를 두 번 append | 같은 위치를 반환하고 중복 기록하지 않는다. |
| LogServer 하나 단절 | 3개 중 1개가 응답하지 않음 | 과반으로 append가 성공한다. |
| LogServer 둘 단절 | 3개 중 2개가 응답하지 않다가 복구 | 동일 위치에서 대기·재시도하고 복구 후 commit한다. |
| pending command 뒤 새 command | 첫 append가 비정족수인 동안 다음 append 도착 | 첫 위치를 먼저 commit하고 다음 위치를 사용한다. |
| 요청 취소 후 동일 command 재시도 | HTTP 호출자가 대기를 취소한 뒤 연결 복구 | registry 소유 작업은 계속되고 재호출은 같은 결과에 합류한다. |

이 파일이 사용하는 `tests/support/native_loglet.py`는 테스트 전용 DSL이다. `append`,
`disconnect`, `reconnect` 같은 프로토콜 동사만 제공한다. assertion이나 재시도 정책은
감추지 않는다.

## 2. NativeLoglet `checkTail`

다음으로 `tests/unit/native_loglet/test_check_tail.py`를 읽는다.

| 관측 상태 | 시나리오 | 기대 결과 |
|---|---|---|
| none sealed, empty | local tail이 모두 0 | `(0, false)` |
| none sealed, quorum copy | 같은 tail이 과반에 저장됨 | 그 tail을 open 상태로 반환 |
| none sealed, one physical copy | commit 증거가 없음 | local-tail-or-seal notification을 기다림 |
| none sealed, client `knownTail` | client가 이미 global tail을 앎 | 알려진 tail을 open 상태로 반환 |
| some sealed | seal 도중 zombie copy가 한 곳에만 존재 | 전부 seal하고 max tail까지 repair |
| all sealed, conflicting copy | 같은 위치에 서로 다른 명령 | `EntryConflict`; 임의 값을 선택하지 않음 |

`write_local_copy`는 sequencer를 우회한다. 이름 그대로 물리 복사본만 만들며 globally
committed라고 주장하지 않는다. 이 구분 때문에 테스트의 setup 자체가 UI의 “Observed
physical entries”와 같은 의미를 가진다.

`test_protocol_properties.py`는 무작위 LogServer failure schedule에서도 append 위치가
dense하게 유지되고, sealed repair가 임의의 단일 sequencer 값을 복구하며, trim watermark가
임의 순서의 요청에서 뒤로 가지 않는지 반복 생성해 확인한다.

## 3. VirtualLog 주소와 재구성

그다음 세 파일을 순서대로 읽는다.

1. `tests/unit/virtual_log/test_reconfigure.py`: seal, first-unwritten tail, 반개구간 연결,
   경쟁 CAS 한 승자, seal/CAS 전·후 클라이언트 종료와 roll-forward,
   prefix trim과 sealed segment truncate/modify.
   Native configuration 변경 전 데이터 준비와 검증은
   `tests/unit/native_loglet/test_replacement.py`에서 별도로 읽는다.
2. `tests/unit/virtual_log/test_append.py`: cached LogChain fast path, sealed segment refresh와
   opaque configuration clone.
3. `tests/unit/virtual_log/test_read.py`: 세그먼트 경계 routing, local-first fallback,
   closed range 밖 zombie 격리.

여기서는 별도 fluent DSL을 두지 않았다. `VirtualLog.reconfig_extend`, `append`, `read` 자체가
이미 논문의 공통 API이고, 추가 DSL은 오히려 어떤 호출이 실제 production contract인지
가릴 가능성이 크기 때문이다.

## 4. MetaStore와 Paxos

- `tests/unit/metastore/test_memory.py`: 버전된 CAS 레지스터와 동시 한 승자.
- `tests/unit/metastore_paxos/test_acceptor.py`: promise, accept, 결정 순서와 gap.
- `tests/unit/metastore_paxos/test_proposer.py`: 정족수 가용성, 이전 accepted value 복구.
- `tests/unit/metastore_paxos/test_safety_properties.py`: ballot과 chosen value 안전성 속성.
- `tests/integration/metastore_paxos/test_sqlite_restart.py`: promise와 결정의 재시작 영속성.

NativeLoglet sequencer 테스트와 Paxos proposer 테스트를 나란히 읽으면 둘이 왜 같은 leader가
아닌지 드러난다. 전자는 한 Loglet에서 계속 위치를 부여하고, 후자는 MetaStore 명령 하나를
독립 Paxos 슬롯에 결정한다.

## 5. Application과 실제 장애 단위

- `tests/unit/kv/test_service.py`: GET catch-up과 VirtualLog 복구 실패의 application 오류 번역.
- `tests/integration/kv/test_kv_process_demo.py`: 여러 DB 프로세스의 재구성과 재시작 catch-up.
- `tests/integration/controller/test_controller_process.py`: 실제 Controller에서 sequencer가
  있는 Converged 프로세스를 종료하고 다른 DB 노드로 계속 쓰기.
- `frontend/e2e/lab.spec.ts`: 브라우저가 선택한 DB 노드에 쓰고 VirtualLog 투영을 관찰.

새 노드 편입은 다음 계약을 이어서 확인한다.

- `tests/unit/kv/test_kv_sqlite_store.py`: 값, dedup 결과, 적용 위치가 같은 snapshot으로
  이동하며 비어 있지 않은 저장소에는 설치하지 않는다.
- `tests/unit/kv/test_database_bootstrap.py`: snapshot 설치 뒤에 VirtualLog catch-up을
  수행하고, 재시작한 replica에는 snapshot을 덮어쓰지 않는다.
- `tests/unit/native_loglet/test_storage_membership.py`: 준비된 Database 노드가 새 segment부터
  storage member가 된다.
- `tests/integration/controller/test_controller_process.py`: 실제 3-node 클러스터에 새 불투명
  `db-xxxxx` identity를 추가하고 기존 KV 상태, 4-member NativeLoglet, 이후 sequencer
  failover를 함께 확인한다.

## 선택 실행

```bash
mise run pytest -- tests/unit/native_loglet/test_sequencer.py
mise run pytest -- tests/unit/native_loglet/test_check_tail.py
mise run pytest -- tests/unit/virtual_log/test_reconfigure.py
mise run pytest -- tests/unit/metastore_paxos/test_safety_properties.py
mise run pytest -- tests/integration/controller/test_controller_process.py
```

테스트 전용 DSL은 protocol setup의 반복만 줄여야 한다. production 객체를 대신하거나,
여러 네트워크 사건을 한 메서드에 숨기거나 assertion까지 fluent chain에 넣으면, 어떤
production 계약을 검증하는지 드러나지 않아 학습용 코드의 가독성을 해친다.
