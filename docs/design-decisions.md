# 주요 설계 결정

이 문서는 현재 코드에 남기지 않은 대안과 최종 선택의 이유를 기록한다. 구현 이력을
재현하는 일지가 아니라, 이후 변경에서 지켜야 할 경계와 trade-off를 설명한다.

## 로컬 subprocess를 실행 환경으로 사용한다

**결정:** Controller가 Python subprocess와 loopback 포트를 직접 관리한다. Docker와
Compose는 요구하지 않는다.

**이유:** 학습 대상은 이미지·volume·container network 관리가 아니라 VirtualLog,
NativeLoglet, Paxos와 공동 프로세스 장애다. subprocess만으로 프로세스 종료, 재시작,
`SIGSTOP`/`SIGCONT`와 독립 SQLite 영속성을 재현할 수 있다.

**결과:** 설치가 가볍고 OS 프로세스 장애가 명확하다. 반면 container 격리, 실제 overlay
network, 배포 자동화와 운영 topology는 이 저장소의 범위가 아니다.

## Database는 Converged 장애 단위를 사용한다

**결정:** Application, VirtualLog client, NativeLoglet client, LogServer와 segment
sequencer를 한 Database 프로세스에 배치한다.

**이유:** Delos가 설명하는 Converged 배치를 직접 관찰하고, 프로세스 하나의 장애가 데이터
경로 컴포넌트에 함께 미치는 영향을 실험하기 위해서다.

**결과:** 배치는 수렴하지만 패키지는 분리한다. 구체 구현 조립은 `runtime.converged`에만
있고 VirtualLog, NativeLoglet, KV와 Controller 사이의 역방향 의존은 금지한다.

## Controller와 UI는 관찰자다

**결정:** Controller는 subprocess 수명주기, Database bootstrap 조율, typed state 수집과
브라우저 프록시만 담당한다. UI는 그 투영을 표시한다.

**이유:** 실험 도구가 seal, tail, membership 또는 LogChain을 직접 결정하면 Delos 구현의
정확성이 Controller 가용성이나 UI 동작에 의존하게 된다.

**결과:** Controller의 polling과 관찰 실패가 프로토콜 상태를 전진시키거나 결정하지
않는다. Controller 종료는 자신이 시작한 자식 프로세스를 정리하지만 protocol state에
별도 결정을 기록하지 않는다. `reachable`이나 UI의 latest observation은 합의 권위로
해석하면 안 된다.

## 일반화된 장애주입 대신 프로세스 lifecycle을 제공한다

**결정:** UI는 Database의 Resume, Pause, Kill과 MetaStore의 Resume, Pause를 제공한다.
transport rule 편집기, scripted scenario와 event timeline은 제공하지 않는다.

**이유:** 현재 학습 시나리오의 핵심은 공동 장애 단위의 정지, timeout, 재구성과 재시작
영속성이다. 범용 장애주입 제어면은 Controller가 프로토콜 전송에 침습할 가능성과 UI
복잡도를 크게 늘린다.

**결과:** `Pause`는 timeout을 만들고 같은 프로세스를 `Resume`할 수 있다. Database
`Kill`은 process identity를 퇴역시키며 목록에서 제거한다. 고정 Paxos membership을
실수로 바꾸지 않도록 MetaStore에는 Kill을 제공하지 않는다.

## 프로세스 identity는 불투명하고 재사용하지 않는다

**결정:** Database member ID와 process ID는 `db-xxxxx` 형식의 짧은 랜덤 문자열 하나를
사용한다. 퇴역한 identity는 tombstone으로 남겨 재사용하지 않는다.

**이유:** 단조 카운터는 목록에서 제거된 프로세스와 새 incarnation을 같은 논리 항목처럼
보이게 할 수 있다. 과거 LogChain은 퇴역한 ID를 계속 참조하므로 endpoint 해석 이력도
필요하다.

**결과:** 테스트와 UI는 순차 ID를 가정하지 않고 전체 불투명 ID를 API target으로 사용한다.

## MetaStore membership은 고정한다

**결정:** Paxos MetaStore peer 수와 identity는 runtime 생성 시 고정한다.

**이유:** Delos 논문의 동적 membership까지 구현하려면 Paxos 값, 재시작, quorum 전이와
형식 모델을 함께 확장해야 한다. 이를 부분적으로 흉내 내는 것보다 현재 학습 범위를
명시하는 편이 정확하다.

**결과:** 온라인 MetaStore node 추가·제거는 지원하지 않는다. 이 차이는
[논문-구현 대조표](paper-implementation-map.md)에 제외 범위로 기록한다.

## Reset은 Controller가 소유한 파일만 제거한다

**결정:** 초기화는 manifest가 가리키는 SQLite, WAL, process log와 manifest만 삭제하고
설정된 최초 규모로 새 cluster를 부트스트랩한다.

**이유:** runtime 디렉터리 전체를 재귀 삭제하면 잘못 지정된 경로의 사용자 파일을 지울
위험이 있다.

**결과:** reset 대상은 runtime 디렉터리 안의 관리 파일인지 검증한다. 임의의 추가 파일은
삭제하지 않는다.
