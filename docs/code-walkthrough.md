# 코드로 따라가는 Delos Lab

이 문서는 [개념 지도](learning-guide.md)를 읽은 뒤 실제 구현을 처음 여는 학습자를 위한
코드 독해 순서다. 개념 지도는 “무엇을 의미하는가”를 설명하고, 이 문서는 “그 의미가 어느
계약과 호출 경로에 놓여 있는가”를 설명한다. 논문과 구현 범위의 차이는
[논문-구현 대조표](paper-implementation-map.md), 개별 불변식은
[테스트 학습 지도](test-scenarios.md)에서 확인한다.

파일 전체를 위에서 아래로 읽기보다 다음 원칙을 지키면 계층을 덜 혼동한다.

- 실행 조립 지점에서 객체 관계를 먼저 본 뒤, 각 패키지가 소유한 계약으로 내려간다.
- `Protocol`과 값 타입을 구현 클래스보다 먼저 읽는다.
- 정상 PUT 하나를 끝까지 따라간 뒤에 장애와 재구성을 읽는다.
- Controller와 UI는 Delos 상태를 결정하지 않으므로 코어를 이해한 뒤 마지막에 읽는다.
- `node`, `process`, database `replica`, LogServer, sequencer, Paxos proposer를 서로 바꾸어
  부르지 않는다.

## 1. 저장소 지도

| 영역 | 핵심 책임 | 첫 파일 |
|---|---|---|
| `virtual_log` | 공통 Loglet·MetaStore 계약, LogChain, 가상 주소와 재구성 | [`virtual_log/loglet.py`](../src/delos_lab/virtual_log/loglet.py) |
| `native_loglet` | sequencer, LogServer, quorum, seal/checkTail/trim, adapter | [`native_loglet/config.py`](../src/delos_lab/native_loglet/config.py) |
| `metastore/paxos` | 버전 레지스터 명령을 결정하는 Classic Paxos | [`metastore/paxos/client.py`](../src/delos_lab/metastore/paxos/client.py) |
| `kv` | 로그 명령, materialization, SQLite 상태와 새 replica bootstrap | [`kv/service.py`](../src/delos_lab/kv/service.py) |
| `runtime` | 구체 구현을 한 Converged DB 프로세스로 조립 | [`runtime/converged_process.py`](../src/delos_lab/runtime/converged_process.py) |
| `controller` | subprocess 수명주기, 관측 수집, HTTP 프록시 | [`controller/process.py`](../src/delos_lab/controller/process.py) |
| `frontend` | Controller 투영을 읽어 학습 화면으로 표현 | [`frontend/src/App.tsx`](../frontend/src/App.tsx) |

`runtime`은 의존성을 연결하는 composition root다. 배치가 Converged라는 이유로
`virtual_log`, `native_loglet`, `kv`의 코드 소유권이 합쳐지는 것은 아니다.
[`test_dependency_boundaries.py`](../tests/unit/test_dependency_boundaries.py)는 이 방향을
정적 import 검사로 고정한다.

## 2. 첫 30분: 계약부터 조립까지

### 2.1 실행 객체 그래프를 한 번 본다

[`serve_db_peer`](../src/delos_lab/runtime/converged_process.py)는 한 DB 프로세스 안에 다음
객체를 만든다.

```text
SQLiteKvStore <── KvMaterializer <── KvService
                                      │
                                      ▼
                                  VirtualLog
                                  ├─ PaxosMetaStoreClient
                                  ├─ HttpNativeLogletProvider
                                  └─ NativeLogletReconfigurationPolicy

SQLiteLogletStore <── NativeLogServer
HttpLogletTransport <── LogServerSequencerRegistry
```

여기서는 생성 순서와 주입 방향만 본다. `serve_db_peer`가 프로토콜 결정을 내린다고 읽으면
안 된다. 이 함수는 구체 구현을 선택하고 수명을 관리한다. HTTP 경로의 결합도
[`create_converged_app`](../src/delos_lab/runtime/converged_http.py) 한 곳에 제한된다.

### 2.2 공통 계약을 읽는다

다음 세 파일은 NativeLoglet이나 Paxos를 알지 않는다.

1. [`virtual_log/loglet.py`](../src/delos_lab/virtual_log/loglet.py)
   - `VirtualLoglet`: `append`, `check_tail`, `read_next`, `prefix_trim`, `seal`
   - `LogletProvider`: `LogSegment`에 맞는 구현을 반환하는 포트
   - `LogletAppend`, `LogletTail`, `LogletEntry`: 구현 독립 결과 타입
2. [`virtual_log/metastore.py`](../src/delos_lab/virtual_log/metastore.py)
   - `MetaStore`: 버전 레지스터의 `read`, `compare_and_set`
   - `Applied`, `VersionMismatch`: CAS 결과
3. [`virtual_log/types.py`](../src/delos_lab/virtual_log/types.py)
   - `LogSegment`: 가상 반개구간과 불투명 Loglet 설정
   - `LogChain`: 마지막 open segment를 포함한 순서 있는 mapping
   - `VersionedLogChain`: MetaStore가 버전과 함께 결정하는 값

이 단계의 확인 질문은 하나다. “`virtual_log`가 `storage_members`나
`sequencer_incarnation`을 해석하는가?” 답은 아니어야 한다. 해당 스키마의 소유자는
[`NativeLogletConfiguration`](../src/delos_lab/native_loglet/config.py)이다.

### 2.3 VirtualLog을 읽는다

[`VirtualLog`](../src/delos_lab/virtual_log/core.py)은 두 종류의 일을 한다.

- `append`, `read_next`, `check_tail`, `prefix_trim`: 가상 주소를 active 또는 해당
  segment의 로컬 주소로 변환하고 공통 Loglet 계약에 위임한다.
- `reconfig_extend`, `reconfig_truncate`, `reconfig_modify`: seal과 tail을 경계로 새
  LogChain 후보를 만들고 MetaStore CAS로 설치한다.

먼저 `bootstrap -> refresh -> append -> read_next`를 읽고, 그다음
`reconfig_extend -> _install`을 읽는다. `_roll_forward`와 `_replace_unavailable`은 이름이
비슷하지만 책임이 다르다.

- `_roll_forward`: 누군가 seal한 뒤 CAS 전에 멈춘 재구성을 완료한다. opaque 설정을
  해석하지 않고 새 segment ID로 이어 붙인다.
- `_replace_unavailable`: open Loglet이 불가용할 때 Loglet별 정책에 후속 설정 선택을
  맡긴다.

## 3. 정상 PUT 한 건을 끝까지 추적하기

브라우저에서 보낸 PUT은 다음 경로를 지난다.

```text
frontend createLabApi.kvPut
  → Controller proxy_kv
  → DB install_kv_api.put
  → KvService.submit
  → VirtualLog.append
  → RemoteNativeLogletRuntime.append
  → HttpSequencerTransport.append
  → LogServerSequencerRegistry.append
  → NativeSequencer.append/_commit
  → LogServer quorum
  → KvMaterializer.materialize_through
  → SQLiteKvStore.apply
```

### 3.1 HTTP는 명령을 만들고 전달한다

- [`frontend/src/api.ts`](../frontend/src/api.ts)의 `createLabApi`는 선택한 DB process를
  URL에 넣는다.
- [`controller/http_api.py`](../src/delos_lab/controller/http_api.py)의 `proxy_kv`는 요청을
  해당 endpoint로 전달한다. Controller가 명령을 로그에 넣거나 DB를 대신 선택하지 않는다.
- [`kv/http_api.py`](../src/delos_lab/kv/http_api.py)의 `put`은 입력을
  `KvCommandEnvelope`로 바꾸고 `KvService.submit`을 호출한다.

`client_id + request_id`로 만든 `command_id`는 단순 HTTP 추적 ID가 아니다. 같은 요청의
재시도를 같은 논리 명령으로 식별해 Loglet 위치와 application 결과를 중복 생성하지 않게
한다.

### 3.2 KV 서비스는 로그를 사용한다

[`KvService.submit`](../src/delos_lab/kv/service.py)은 먼저 SQLite의 완료 요청 기록을
확인한다. 같은 identity와 다른 payload면 `RequestConflict`, 같은 명령이면 저장된 결과를
반환한다. 새 명령이면 다음 순서다.

1. `ensure_bootstrapped`가 cached chain을 사용하거나 MetaStore에서 읽는다.
2. 체인이 전혀 없을 때만 `LogletBootstrapPolicy.initial_segment`로 최초 segment를 만든다.
3. `VirtualLog.append(command_id, payload)`가 반환한 가상 위치를 받는다.
4. `KvMaterializer.materialize_through(position)`으로 그 위치까지 순서대로 적용한다.

KV 계층은 sequencer 후보를 고르거나 seal하지 않는다. `LogletSealed`와
`LogletUnavailable`이 VirtualLog의 제한된 재시도 뒤에도 남을 때만 application의
`ReconfigurationUnavailable`로 번역한다.

### 3.3 adapter가 불투명 설정을 Native 호출로 바꾼다

[`HttpNativeLogletProvider.get`](../src/delos_lab/native_loglet/virtual_log_adapter.py)은
segment별 `RemoteNativeLogletRuntime`을 만든다. 이 adapter가 처음으로 opaque parameters를
`NativeLogletConfiguration`으로 해석한다.

`RemoteNativeLogletRuntime.append`는 설정에 기록된 `sequencer_node`로 요청을 보내고,
응답의 로컬 위치와 `known_tail`을 공통 `LogletAppend`로 바꾼다. VirtualLog은 여기에
`active.virtual_start`를 더해 가상 위치를 만든다.

### 3.4 sequencer가 위치를 배정하고 quorum commit한다

HTTP 요청은 [`LogServerSequencerRegistry`](../src/delos_lab/native_loglet/sequencer_registry.py)에
도착한다. registry는 다음 두 fence를 모두 확인한다.

- segment 설정의 `sequencer_node`가 현재 process ID인가?
- `sequencer_incarnation`이 이 프로세스 시작 때 만든 incarnation ID인가?

둘 중 하나라도 다르면 현재 프로세스가 같은 node ID로 재시작됐더라도 옛 segment의
sequencer가 될 수 없다. `observe`는 UI 상태 조회 때문에 sequencer 인스턴스를 새로 만들지
않는다는 점도 확인한다.

[`NativeSequencer.append`](../src/delos_lab/native_loglet/sequencer.py)는 한 segment 안에서
위치를 단조 증가시키고 `_commit`에서 storage member 과반의 응답을 기다린다. 일시적인
비정족수에서는 위치를 버리지 않고 같은 entry를 재시도한다. registry가 pending task를
HTTP 요청 수명과 분리하므로 호출자가 timeout되어도 commit 또는 quorum seal 판정까지
작업이 이어질 수 있다.

### 3.5 materializer가 로그와 상태 머신을 연결한다

[`KvMaterializer.materialize_through`](../src/delos_lab/kv/materializer.py)은 로컬
`applied_position + 1`부터 목표 위치까지 `VirtualLog.read_next`로 재생한다. payload를 다시
`KvCommandEnvelope`로 검증하고
[`SQLiteKvStore.apply`](../src/delos_lab/kv/sqlite_store.py)에 넘긴다.

순수 명령 의미는 [`KvStateMachine.apply`](../src/delos_lab/kv/state_machine.py)에 있다.
SQLite store는 값, 요청 dedup 결과, 적용 위치를 같은 트랜잭션에서 영속화한다. 따라서
append 성공과 이 DB replica의 materialization 완료는 서로 다른 사건이며, PUT 응답은 후자까지
완료한 뒤 반환된다.

## 4. GET과 tail을 추적하기

GET은 로그 엔트리를 추가하지 않는다.

```text
KvService.get
  → KvService.sync
  → VirtualLog.check_tail
  → NativeLogletClient.check_tail
  → KvMaterializer.materialize_through(tail - 1)
  → SQLiteKvStore.get
```

[`NativeLogletClient.check_tail`](../src/delos_lab/native_loglet/client.py)을 읽을 때 세 값을
분리한다.

- `local_tail`: 한 LogServer의 물리 high-water mark
- global tail: globally committed인 dense prefix의 첫 unwritten position
- `known_tail`: 이 client가 알고 있는 global tail의 단조 증가 하한

open Loglet에서 가장 긴 물리 복사본 하나만으로 global tail을 선언하지 않는다. 과반 copy,
이미 알려진 global tail 또는 빈 로그라는 증거가 없으면 `wait_for_tail` notification을
기다린 뒤 다시 판단한다. 일부 서버만 sealed면 seal을 전파한다. 모두 sealed면 최대 tail까지
누락 copy를 repair한 후 고정된 tail을 반환한다.

`VirtualLog.check_tail`은 이 로컬 tail에 active segment의 `virtual_start`를 더한다.
`KvService.sync`는 첫 unwritten 위치에서 1을 뺀 곳까지 materialize한다. UI의 `knownTail`은
이 호출을 방금 수행한 권위 결과가 아니라 각 컴포넌트가 현재 기억하는 하한이다.

## 5. sequencer 장애와 재구성 추적하기

활성 sequencer process가 Pause되면 `HttpSequencerTransport.append`가 응답을 받지 못해
`LogletUnavailable`로 번역한다. 이후 흐름은 다음과 같다.

```text
VirtualLog.append
  → _replace_unavailable(active)
  → NativeLogletReconfigurationPolicy.successor
  → live incarnation을 가진 다음 storage member 선택
  → reconfig_extend(new configuration)
      → old runtime.seal()
      → old runtime.check_tail()
      → old.virtual_stop / new.virtual_start 계산
      → MetaStore.compare_and_set(old version, candidate chain)
  → 새 active segment에서 원래 command_id 재시도
```

[`NativeLogletReconfigurationPolicy`](../src/delos_lab/native_loglet/reconfiguration.py)는
Native 설정을 소유하므로 storage member 순서를 읽고 새 sequencer와 incarnation을 선택할 수
있다. 반대로 [`VirtualLog.reconfig_extend`](../src/delos_lab/virtual_log/core.py)는 설정을
해석하지 않고 공통 seal, tail, CAS 순서만 보장한다.

seal 이후 모든 client가 성공하는 것은 아니다. 여러 DB가 동시에 같은 old version에 대해
후보 체인을 만들 수 있고 MetaStore CAS의 한 후보만 설치된다. `_install`은 승패와 관계없이
MetaStore가 반환한 실제 chain을 cache한다. 따라서 CAS 패배는 별도의 권위 체인을 만드는
실패가 아니다.

이미 sealed인 active를 발견한 경로는 `_roll_forward`다. 다른 client가 seal과 CAS 사이에
종료됐을 수 있으므로 MetaStore를 다시 확인하고, 유예 뒤에도 같은 active면 동일한 opaque
설정의 새 segment를 설치한다. 이것은 불가용 sequencer를 고르는 정책과 별개다.

## 6. MetaStore의 Paxos를 추적하기

VirtualLog이 보는 계약은 `read`와 versioned CAS뿐이다. Paxos는 이 포트 아래에 있다.

```text
PaxosMetaStoreClient
  → 한 reachable MetaStore peer
  → PaxosMetaStore.read / compare_and_set
  → PaxosProposer.propose
  → 독립 Paxos slot의 Prepare → Accept → Decide
  → PaxosAcceptor가 순서대로 적용
  → VersionRegisterStateMachine.apply
```

읽기 순서는 다음이 좋다.

1. [`metastore/paxos/types.py`](../src/delos_lab/metastore/paxos/types.py)에서 ballot, 요청,
   `ReadBarrierCommand`, `CompareAndSetCommand`를 본다.
2. [`PaxosProposer`](../src/delos_lab/metastore/paxos/proposer.py)에서 이전 accepted value 채택과
   quorum 결정을 본다.
3. [`PaxosAcceptor`](../src/delos_lab/metastore/paxos/acceptor.py)에서 promise, accept, decide와
   순차 적용을 본다.
4. [`VersionRegisterStateMachine`](../src/delos_lab/metastore/paxos/state_machine.py)에서 CAS가
   version을 어떻게 전진시키는지 본다.
5. [`sqlite_storage.py`](../src/delos_lab/metastore/paxos/sqlite_storage.py)에서 promise,
   accepted/decided value와 적용 상태의 영속화를 확인한다.

`read`도 `ReadBarrierCommand`를 새 Paxos slot에 결정한다. 이 구현은 고정 membership의
독립 Classic Paxos slot을 사용하며 stable Multi-Paxos leader 최적화는 없다.
`PaxosProposer`는 MetaStore 명령 하나를 결정하려는 역할이고, NativeLoglet sequencer는 한
segment에서 계속 로그 위치를 부여한다. 둘을 “leader”라는 한 단어로 합치면 코드의 두 합의
경로를 잘못 이해하게 된다.

## 7. 새 Database process 편입 추적하기

UI의 `Add database process`는 단순히 subprocess를 목록에 추가하는 작업으로 끝나지 않는다.

```text
POST /api/database-nodes
  → LabController.add_database_node
  → SubprocessNodeSupervisor.add_database_node
  → 새 ID와 endpoint를 manifest에 pending 상태로 추가
  → --join-existing-database로 Converged process 시작
  → DatabaseReplicaBootstrapper.run
      → 기존 replica에서 application snapshot 가져오기
      → 새 SQLite KV store에 원자 설치
      → VirtualLog tail까지 replay
  → 새 process /health 확인
  → POST /internal/native-loglet/storage-membership/join
  → NativeLogletStorageMembership.join
  → 준비된 새 LogServer를 포함한 새 segment reconfigExtend
  → manifest의 pending 표시 해제
```

[`DatabaseReplicaBootstrapper`](../src/delos_lab/kv/bootstrap.py)는 application state 준비를
담당한다. snapshot에는 KV 값뿐 아니라 request dedup 결과와 `applied_position`이 포함된다.
필요한 suffix가 trim돼 catch-up할 수 없으면 더 최신 snapshot으로 다시 기준을 잡는다.

그 다음 [`NativeLogletStorageMembership.join`](../src/delos_lab/native_loglet/membership.py)이
LogServer membership을 바꾼다. 권위 있는 membership은 Controller manifest가 아니라 새
LogSegment의 `NativeLogletConfiguration.storage_members`다. 과거 sealed segment는 예전
membership을 그대로 유지한다.

여기서 database `replica`는 같은 application log를 materialize한 상태 사본을 뜻한다.
새 process가 이 상태를 준비했다는 사실과, 새 segment에서 LogServer가 됐다는 사실은 서로
다른 계약이며 반드시 이 순서로 진행된다.

## 8. Controller와 UI는 마지막에 읽는다

코어 경로를 이해한 뒤 다음 순서로 관측 계층을 읽는다.

1. [`controller/deployment.py`](../src/delos_lab/controller/deployment.py): UI로 보내는 typed
   observation 모델
2. [`DeploymentCollector`](../src/delos_lab/controller/topology.py): subprocess lifecycle과
   `/state` 또는 `/paxos/state`를 읽어 투영
3. [`controller/http_api.py`](../src/delos_lab/controller/http_api.py): process action, reset,
   add와 요청 proxy
4. [`frontend/src/types.ts`](../frontend/src/types.ts): Controller 투영의 TypeScript mirror
5. [`frontend/src/observations.ts`](../frontend/src/observations.ts): 관측된 MetaStore 상태 중
   가장 높은 version의 chain 선택
6. [`TopologyComponents.tsx`](../frontend/src/components/TopologyComponents.tsx),
   [`VirtualLogView.tsx`](../frontend/src/components/VirtualLogView.tsx): 학습 화면 표현

[`create_converged_app`](../src/delos_lab/runtime/converged_http.py)의 `/state`는 application,
cached VirtualLog, NativeLoglet client, sequencer registry와 LogServer의 로컬 관측을 한 응답에
모은다. 이 endpoint와 Controller polling은 append, seal, checkTail 또는 Paxos 결정을
호출하지 않는다.

UI의 `latestObservedChain`도 새 권위가 아니다. 각 MetaStore peer에서 관측된 상태 중 가장
높은 version을 표시하기 위한 projection이다. `running`, `reachable`, `knownTail`, physical
entry copy를 protocol readiness, 확정 tail 또는 globally committed entry로 승격해 읽지
않는다.

## 9. 코드와 테스트를 짝지어 읽기

| 구현 질문 | 구현 파일 | 가장 가까운 테스트 |
|---|---|---|
| 같은 명령을 재시도하면 위치가 바뀌는가? | `native_loglet/sequencer.py`, `sequencer_registry.py` | [`test_sequencer.py`](../tests/unit/native_loglet/test_sequencer.py) |
| open/sealed tail은 어떤 증거로 정해지는가? | `native_loglet/client.py`, `server.py` | [`test_check_tail.py`](../tests/unit/native_loglet/test_check_tail.py) |
| seal 후 누락 copy는 어떻게 수선되는가? | `native_loglet/client.py::_repair_through` | [`test_check_tail.py`](../tests/unit/native_loglet/test_check_tail.py) |
| 가상 segment 경계가 겹치지 않는가? | `virtual_log/core.py::reconfig_extend` | [`test_reconfigure.py`](../tests/unit/virtual_log/test_reconfigure.py) |
| zombie copy가 가상 범위에 노출되는가? | `virtual_log/core.py::read_next` | [`test_read.py`](../tests/unit/virtual_log/test_read.py) |
| trim watermark가 뒤로 가는가? | `native_loglet/client.py`, stores | [`test_prefix_trim.py`](../tests/unit/native_loglet/test_prefix_trim.py) |
| sealed segment 설정 교체 전 데이터가 준비되는가? | `native_loglet/replacement.py` | [`test_replacement.py`](../tests/unit/native_loglet/test_replacement.py) |
| Paxos가 이전 accepted value를 버리는가? | `metastore/paxos/proposer.py` | [`test_proposer.py`](../tests/unit/metastore_paxos/test_proposer.py) |
| promise와 결정이 재시작 뒤 남는가? | `metastore/paxos/sqlite_storage.py` | [`test_sqlite_restart.py`](../tests/integration/metastore_paxos/test_sqlite_restart.py) |
| 새 DB가 준비 전에 quorum에 들어가는가? | `kv/bootstrap.py`, `native_loglet/membership.py` | [`test_database_bootstrap.py`](../tests/unit/kv/test_database_bootstrap.py), [`test_storage_membership.py`](../tests/unit/native_loglet/test_storage_membership.py) |
| Controller가 코어 구현을 import하는가? | 패키지 전체 | [`test_dependency_boundaries.py`](../tests/unit/test_dependency_boundaries.py) |

테스트 이름을 먼저 읽고, setup에서 어떤 failure schedule과 물리 상태를 만들었는지 본 뒤,
마지막으로 assertion을 읽는다. 테스트 helper의 `write_local_copy`는 commit이 아니라 물리
복사본만 만든다는 식으로 동사의 의미를 정확히 유지해야 한다.

## 10. 독해 체크포인트

코드를 수정하기 전에 다음 질문에 파일과 심볼 이름으로 답할 수 있으면 전체 경계를 이해한
것이다.

1. LogChain의 유일한 설치 지점과 경쟁 제어는 어디인가?
2. NativeLoglet 설정을 처음 해석하는 계층은 어디인가?
3. sequencer의 `knownTail`과 한 LogServer의 `local_tail`은 각각 누가 소유하는가?
4. seal과 MetaStore CAS 사이에서 프로세스가 죽으면 누가 작업을 이어가는가?
5. Paxos proposer와 NativeLoglet sequencer가 공유하지 않는 책임은 무엇인가?
6. PUT이 quorum commit된 뒤에도 왜 materializer 단계가 필요한가?
7. 새 DB process가 application replica와 LogServer membership에 들어가는 순서는 무엇인가?
8. Controller의 관측이 실패해도 프로토콜의 안전성 정의가 달라지면 안 되는 이유는
   무엇인가?
9. UI에 보이는 최신 chain과 `knownTail`이 왜 새로운 권위 상태가 아닌가?
10. Converged process 하나가 죽을 때 함께 실패하는 컴포넌트와, 코드상 분리돼야 하는
    패키지는 무엇인가?

막히면 구현을 무작정 넓게 읽지 말고 해당 질문의 `Protocol -> concrete implementation ->
composition root -> test` 순서만 다시 따라간다. 이 순서를 지키면 학습용 단순화와 Delos의
핵심 계약을 분리해서 판단할 수 있다.
