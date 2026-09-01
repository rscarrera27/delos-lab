# 공개 및 릴리스 체크리스트

이 목록은 저장소를 처음 공개하거나 새 버전을 태그하기 직전에 사용한다. 자동화된 CI를
가정하지 않으므로 각 명령의 결과를 로컬에서 확인한다.

## 저장소와 이력

- [ ] `git status --short`의 추가·수정·삭제가 모두 의도한 변경이다.
- [ ] runtime DB, WAL, log, frontend build, test report와 editor 파일이 추적되지 않는다.
- [ ] 내부 계획, 임시 검토 기록과 사용하지 않는 Docker/장애주입 코드가 공개 tree에 없다.
- [ ] commit history를 함께 공개할지, 검토 가능한 한 개의 초기 commit으로 정리할지
      의식적으로 결정했다.
- [ ] private key, access token, password, 개인 경로가 현재 tree와 공개할 history에 없다.

## 문서와 메타데이터

- [ ] README Quick Start를 빈 runtime에서 그대로 실행했다.
- [ ] README, `docs/README.md`와 상대 Markdown link가 유효하다.
- [ ] 논문-구현 대조표가 구현·축소·제외 범위를 정확히 반영한다.
- [ ] 용어가 개념 지도와 일치하고 `replica`를 일반 프로세스 동의어로 쓰지 않는다.
- [ ] `pyproject.toml`의 버전, Python 범위, repository URL과 license가 맞다.
- [ ] `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`가 포함돼 있다.

## 코드 검증

```bash
mise run check
mise run setup:e2e
mise run test:e2e
```

- [ ] 실제 Controller에서 3 MetaStore + 3 Database가 reachable이 됐다.
- [ ] PUT으로 초기 LogChain을 만들고 Virtual Log에서 관찰했다.
- [ ] sequencer process Pause 후 다른 DB 요청이 새 segment에서 성공했다.
- [ ] Database process 추가가 snapshot, replay와 새 storage membership을 완료했다.
- [ ] 초기화가 기존 값을 제거하고 설정된 최초 규모로 다시 시작했다.

## 선택적 형식 모델

TLC를 설치한 환경에서는 [formal/README.md](../formal/README.md)의 두 명령을 실행하고 모든
invariant가 통과하는지 확인한다. 이 결과는 유한 모델 검사이며 Python 구현의 증명이라고
표현하지 않는다.

## 공개 직전

- [ ] `git diff --check`가 통과한다.
- [ ] 공개할 branch와 tag가 의도한 commit을 가리킨다.
- [ ] 저장소 설명이 “Delos 전체 구현”이나 운영 준비 상태를 주장하지 않는다.
- [ ] 기본 branch에서 Quick Start와 문서 링크를 다시 확인했다.
