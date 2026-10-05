# gx3-cli-mcp

[日本語](README.ja.md) · [English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<!-- mcp-name: io.github.purinzan/gx3-cli-mcp -->

[![PyPI](https://img.shields.io/pypi/v/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![CI](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-source--available-blue)](LICENSE.txt)
[![gx3-cli-mcp MCP server](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp/badges/score.svg)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

**GX Works3를 열지 않고도 코일이 ON이 되지 않는 이유를 알아보세요.**

로컬 컴퓨터에 있는 미쓰비시전기 MELSEC의 GX Works3 `.gx3` 프로젝트를 읽고,
디바이스에 값을 쓰는 위치, 코일이 ON이 되기 위해 충족되어야 할 조건,
PLC 외부에서 오는 조건, 절대로 참이 될 수 없는 분기를 래더에서 확인합니다.
읽기 전용이며 원본 프로젝트에 다시 쓰지 않습니다.

CLI로 사용할 수 있고, 같은 분석을 stdio MCP 서버로도 제공합니다.
AI 에이전트는 바이너리 파일의 내용을 추측하는 대신 인덱싱된 사실을 바탕으로 답할 수 있습니다.

---

## 설치

```bash
pip install gx3-cli-mcp
```

Python 3.10 이상이 필요합니다. `gx3-cli`와 `gx3-mcp-server`라는 두 콘솔 스크립트가 설치됩니다.

## 30초 만에 시작하기

기존 프로젝트가 없어도 됩니다. 먼저 하나를 생성하세요.

```bash
gx3-cli synthetic-project demo.gx3 --profile demo-line
gx3-cli guide --root demo.gx3
```

`guide`는 프로젝트를 읽고, 해당 프로젝트에서 실행할 만한 명령과 그 이유를 알려줍니다.
"명령이 60개나 있는데 어디서 시작해야 할까?"라는 질문에 답해 줍니다.

## 프로젝트에서 사용하기

```bash
gx3-cli doctor --root project.gx3        # does it read?
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli guide --root project.gx3         # what to run next
```

이제 궁금한 내용을 확인하세요.

```bash
# where is this device written, and what reads it?
gx3-cli xref where-used M100 --root project.gx3

# why is this coil not turning on?
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact

# the whole program, one line per rung
gx3-cli rung-text --root project.gx3

# search the comment you remember, not the device number you don't
gx3-cli query-comment "clamp pressure" --root project.gx3
```

각 명령은 스크립트 처리를 위한 `--format json`과 화면에 출력하는 대신 파일로 저장하는
`-o FILE`을 지원합니다. `gx3-cli --help`는 모든 명령을 그룹별로 보여 줍니다.

`.gx3`를 전달하면 `.gx3_cache/<sha256>/`에 압축을 풀고 그 복사본을 분석합니다.

## 확인할 수 있는 내용

| 질문 | 명령 |
|---|---|
| 이 코일이 OFF인 이유는 무엇인가요? | `trace-device`, `interlock-check` |
| 이 디바이스의 값을 쓰거나 읽는 위치는 어디인가요? | `xref where-used`, `xref downstream` |
| 이 프로그램은 어떤 일을 하나요? | `rung-text`, `ladder-print`, `metrics` |
| 래더의 렁(회로)을 그림으로 보고 싶어요 | `ladder-layout --format svg` |
| PLC 외부에서 무엇이 들어오나요? | `external-inputs`, `comm-refresh` |
| 절대로 참이 될 수 없는 조건은 무엇인가요? | `dead-logic` |
| 문제가 있어 보이는 부분은 어디인가요? | `lint PROJECT`(중복 코일, 여러 쓰기 주체, 피연산자 폭, 자료형) |
| 버전 사이에 무엇이 바뀌었나요? | `diff`, `semantic-diff` |
| 프로젝트를 올바르게 읽었나요? | `roundtrip` |

## AI 에이전트와 함께 사용하기

```json
{
  "mcpServers": {
    "gx3": { "command": "gx3-mcp-server" }
  }
}
```

클라이언트가 PATH에서 콘솔 스크립트를 찾지 못한다면
`"command": "python", "args": ["-m", "gx3cli.gx3_mcp_server"]`를 사용할 수 있습니다.
서버는 읽기 전용 분석 도구와 제한된 명령 실행 기능을 제공합니다.

에이전트가 도구를 사용하는 방법은 [에이전트 사용 가이드(일본어)](docs/AGENT_USAGE_JA.md)를 참고하세요.

## 지원 범위와 한계

로컬 `.gx3` 프로젝트의 래더 데이터를 읽기 전용으로 분석합니다.

프로젝트를 편집하거나, 변경을 위해 PLC에 연결하거나, GX Works3를 대체하지 않습니다.
`live-read`는 MC Protocol/SLMP로 실제 디바이스 값을 읽을 수 있지만,
CLI에서만 사용할 수 있고 연결 매개변수를 명시해야 합니다.

출력은 참고 정보입니다. 실제 장비에 손대기 전에 GX Works3와 자체 안전 절차를 통해 검증하세요.

프로젝트 파싱에 실패하면 `gx3-cli failure-corpus capture`로 로컬 회귀 검증용 샘플을 만들 수 있습니다.
데이터를 외부로 전송하지 않습니다.

## 문제 해결

**7z 형식의 `.gx3`** — 7-Zip을 설치하거나 경로를 지정하세요.
`set GX3_7Z=C:\Program Files\7-Zip\7z.exe`. 암호화된 컨테이너는 복호화하지 않습니다.
대신 GX Works3에서 폴더를 내보내세요.

**PyPI에 접근할 수 없는 경우** — `pip install git+https://github.com/purinzan/gx3-cli-mcp.git`

**읽은 결과가 잘못된 경우** — 먼저 `gx3-cli doctor --root ...`를 실행한 다음
[Issue를 작성](https://github.com/purinzan/gx3-cli-mcp/issues/new/choose)하세요.

## 문서

- 사용자 매뉴얼 [일본어](docs/USER_MANUAL_JA.md) / [영어](docs/USER_MANUAL_EN.md) / [중국어](docs/USER_MANUAL_ZH.md)
- [에이전트 사용 가이드(일본어)](docs/AGENT_USAGE_JA.md)
- [래더 실무 팁(일본어)](docs/LADDER_PRACTICAL_TIPS_JA.md) — 현장 작업을 위한 수정 및 검토 팁
- [보안 안내(일본어)](docs/SECURITY_JA.md) — 로컬 데이터 처리와 읽기 전용 MCP 정책
- [검증 작업 관리](https://github.com/purinzan/gx3-cli-mcp/issues/202) — 검증 작업과 인수 조건
- [독립 검증 기록(일본어)](docs/INDEPENDENT_VALIDATION_LEDGER_JA.md) — Issue #49의 GX Works3 검증 근거
- [Doctor 인수 검증 기록(일본어)](docs/DOCTOR_ACCEPTANCE_JA.md) — Issue #135의 프로젝트 상태 인수 검증
- [GX Works3 기능 매트릭스(일본어)](docs/GX_WORKS3_FEATURE_MATRIX_JA.md) — 표준 기능의 지원 범위, 미지원 사항, 구현 우선순위
- [파일 사용 가이드(일본어)](docs/FILE_USAGE_GUIDE_JA.md) — 저장소 구성
- [분석 벤치마크(일본어)](docs/ANALYSIS_BENCHMARK_JA.md) — 합성 데이터 기반 성능 기준과 측정 한계
- [검토 질문(일본어)](docs/REVIEW_QUESTIONS_JA.md) — PR을 열기 전에 변경 사항에 대해 확인할 질문과 각 질문으로 발견한 버그
- [관련 프로젝트(일본어)](docs/GITHUB_PROJECT_REVIEW_JA.md) — 다른 GX Works3/MELSEC 도구와 그 도구에서 도입한 내용
- [llms.txt](llms.txt) — 이 도구의 역할과 지원하지 않는 범위에 대한 기계 판독용 요약

에이전트 스킬: [기존 프로젝트 감사](skills/gx3-existing-project-audit/SKILL.md)
· [실패 코퍼스](skills/gx3-failure-corpus/SKILL.md)

## 분석 계약 마이그레이션

#153의 쿼리별 현재 상태와 남은 인수 검증 작업은
[분석 계약 마이그레이션 기록(일본어)](docs/ANALYSIS_CONTRACT_MIGRATION_JA.md)을 참고하세요.

## 라이선스

**소스 코드를 열람할 수 있는 소프트웨어이며, 오픈 소스는 아닙니다.** 전체 조건은
[LICENSE.txt](LICENSE.txt)에 있습니다. 이 절은 요약이며 라이선스 본문이 우선합니다.

소스 코드를 읽고 평가 및 내부 업무를 위해 실행하는 것은 **허용됩니다**. 회사 내부 사용도 포함됩니다.
서면 허가 없이 재배포하거나, 서비스로 호스팅하거나, 유료 제품에 포함하는 것은 **허용되지 않습니다**.
라이선스 키, 활성화 절차, 유료 요금제는 없습니다.
상업적 이용에 관한 문의는 Issue로 남겨 주세요.

## Glama 등재

MCP 서버로 인덱싱되어 있으며, 각 도구의 설명이 기능을 얼마나 잘 전달하는지에 대한 점수가 제공됩니다.
도구 인터페이스에 대한 외부 피드백으로 활용할 수 있습니다.
점수가 낮은 도구는 설명을 개선할 필요가 있는 도구입니다.

[![gx3-mcp-server on Glama](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp/badges/card.svg)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

기여 안내: [CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

래더 CSV를 입력하려면 `gx3-cli rung-text --csv ladder.csv`를 사용하세요.
검증된 범위와 한계는 [CSV 파서 가이드(일본어)](docs/LADDER_CSV_JA.md)를 참고하세요.
