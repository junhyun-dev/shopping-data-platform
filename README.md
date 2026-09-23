# Shopping Data Platform

결국 **실제 거래 표본의 상품별 숫자를 보고, 그 숫자에 들어간 Excel 원본 행과 계산 SQL까지 확인하는 로컬 서비스**다.

기본 실행은 UCI `Online Retail.xlsx`의 Excel 2–8행과 143행을 다룬다. 같은 보존 파일에서 실행별 선택 행을 바꿔 결과 차이를 확인할 수도 있다. `Quantity`의 부호를 그대로 보존하여 상품 코드와 원문 `InvoiceDate`의 날짜 부분으로 묶고 다음 두 값을 만들며, 상세에서는 `InvoiceNo`에 나타난 원천 취소 표시를 별도로 보여 준다.

- **표본 수량 합계:** 선택한 행들의 `Quantity` 합
- **관찰 행 수:** 그 상품·날짜 묶음에 들어간 선택 행의 수

이 결과는 하루 전체 판매·취소·환불·결제·순매출이 아니다. 특히 `D / -1`인 143행도 원문 그대로 보존할 뿐 업무 의미를 확정하지 않는다.

## 지금 실제로 이어지는 경로

```text
보존한 Excel bytes + source manifest
→ Python이 이번 실행에서 고른 행을 원값으로 적재
→ DuckDB가 타입과 InvoiceNo 원천 표시를 명시한 상세를 제공
→ 실행-원본 행 연결을 거쳐 독립 SQL이 상품·원문 날짜별 표본 합계를 계산
→ 실행별 JSON을 만든 뒤 성공한 결과만 current로 등록
→ 단일 writer 잠금과 DB 증거로 중단된 실행을 다음 실행에서 복구
→ loopback 화면에서 숫자 → 기여 행 → 원본 위치 / SQL 확인
→ 성공한 두 실행을 골라 값과 추가·빠진 원본 위치 비교
```

| 경계 | 현재 역할 |
|---|---|
| `source_files` | 파일 SHA-256, 출처와 보존 위치. 기존 `selected_excel_rows` 열은 첫 등록 당시의 호환용 스냅샷이며 실행 범위의 정본이 아님 |
| `source_rows` | 파일 ID·시트·Excel 행번호, 원래 셀 값과 InvoiceNo 셀 종류. 같은 위치의 기술 재적재만 막음 |
| `run_input_scopes` | 실행이 택한 시트·정렬된 Excel 행 집합, 그 집합의 지문, 적용한 집계 SQL 지문과 원천 변환 SQL 지문 |
| `run_input_rows` | 실행 ID와 이번 계산에 들어간 원본 위치의 연결. 집계·타입 검증·기여 행 조회가 이 연결에 한정됨 |
| `sql/typed_transaction_lines.sql` | 수량·시각 타입과 `InvoiceNo` 원천 취소 표시를 명시한 DuckDB view. 단가는 계산하지 않고 원본에서 읽은 문자열을 보존함. `CustomerID`는 결과 모델에 포함하지 않음 |
| `sql/sample_product_daily.sql` | 실행 ID로 선택 행을 한정한 뒤 상품 코드와 원문 시각의 날짜 부분별 표본 수량 합계·관찰 행 수 |
| `pipeline_runs` | 성공·실패·중단 복구 실행과 writer 소유 흔적. 실패·미완료 실행은 마지막 정상 결과를 바꾸지 않음 |
| `published_results` | 성공한 실행의 JSON 중 화면이 읽을 현재 결과 한 개를 가리킴 |

파일 지문·시트·행번호는 **원본 위치 식별자**이지 주문·거래의 업무 중복키가 아니다. 내용이 같은 서로 다른 원본 행을 자동으로 지우지 않는다.

## 실행

Linux 또는 WSL2, Python 3.10 이상을 사용한다. 프로세스 잠금·중단 검증은 Linux `flock`과 `SIGKILL`에 의존한다. Windows 네이티브 실행은 지원 범위가 아니다.

```bash
git clone https://github.com/junhyun-dev/shopping-data-platform.git
cd shopping-data-platform
python3 -m venv .venv
. .venv/bin/activate
python -m pip install "duckdb==1.4.4" "openpyxl==3.1.5" "pytz==2025.2" "pytest>=9,<10>"
pytest -q
```

데이터를 받기 전에는 가공 테스트 자료로 16개 검사가 통과하고, 실제 UCI 파일을 읽는 1개 검사는 건너뛴다. 실제 자료로 실행하려면 [UCI 공식 다운로드](https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip)에서 ZIP을 받아 `Online Retail.xlsx`만 `data/source/Online Retail.xlsx`에 둔다. 원본 파일은 저장소에 포함하지 않는다.

```bash
mkdir -p data/source
# 내려받은 ZIP 안의 Online Retail.xlsx를 위 폴더에 둔 뒤 실행한다.
python -m shopping_data run
python tools/independent_check.py
pytest -q
python -m shopping_data serve
```

파이프라인은 실행 전에 `config/source.json`의 SHA-256과 파일을 대조한다. 기대 지문은 `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`이며 다르면 중단한다. `independent_check.py`는 별도 openpyxl 계산값을 출력하고, 실제 UCI 테스트는 이와 같은 독립 계산을 SQL 결과와 대조한다. 파일을 받은 뒤에는 실제 원본 검사를 포함해 17개 검사가 실행된다.

같은 파일에서 선택 범위만 바꾸는 실행 예는 다음과 같다. 행 순서는 범위의 뜻을 바꾸지 않으므로 `--rows 3,2`는 `[2,3]`으로 정규화된다.

```bash
python -m shopping_data run --rows 2,3
python -m shopping_data run --rows 3
python -m shopping_data run --rows 3,2
```

브라우저에서 `http://127.0.0.1:8765`를 연다. `처리 이력`에서 성공한 두 실행을 고르면 상품·날짜별 이전/현재 값과 추가·빠진 Excel 행을 확인할 수 있다. 같은 파일·시트·집계 SQL·원천 변환 SQL에서 행 집합만 달라진 경우에만 **선택 범위 변경**으로 표시한다. 파일 지문이나 두 SQL 지문이 달라졌거나 과거 결과에 변환 지문이 없다면 그 기준을 따로 표시하며 늦은 입력이라고 추정하지 않는다.

서버는 `web/`의 명시된 세 파일과 `/api/result`, `/api/runs`, `/api/results`, `/api/comparison`만 제공한다. 저장소·원본 폴더·상위 폴더는 웹으로 제공하지 않는다.

## 실제 프로세스 중단과 복구

파이프라인은 DB별 `<database>.writer.lock`을 `flock`으로 독점한 한 프로세스만 쓰게 한다. 다음 writer는 이 잠금을 얻은 뒤에만 DB에 `running`으로 남은 실행을 복구한다. 살아 있는 실행을 경과 시간만으로 실패 처리하지 않으며, 두 번째 writer는 `PipelineBusy`로 끝난다. 복구를 위해 별도 명령을 외우지 않아도 다음 정상 `python -m shopping_data run`이 먼저 중단 이력을 정리하고 새 실행을 시작한다.

고정된 sleep 대신 자식 프로세스가 준비 신호를 보낸 뒤, 테스트가 직접 소유한 그 PID만 `SIGKILL`하여 다음 네 경계를 확인한다.

| 종료 경계 | 재시작 뒤 확인하는 결과 |
|---|---|
| DB 상세 transaction commit 전 | 상세와 실행-행 연결은 rollback되고 이전 정상 결과가 current로 남는다. |
| 상세 commit 후, 결과 파일 생성 전 | 보존 상세는 남지만 공개 결과는 없고, 다음 writer가 실행을 `중단 복구` 실패로 기록한다. |
| 결과 파일 생성 후, current 게시 전 | 공개 목록에는 들어가지 않는다. 정확한 실행 ID의 파일만 `published/runs/orphaned/`로 옮겨 보존하고 실패 이력과 연결한다. |
| 성공 게시 commit 후, 호출자 반환 전 | 호출자가 응답을 잃어도 DB의 성공·current·결과 파일을 그대로 신뢰하며 실패로 되돌리지 않는다. |

복구 뒤 같은 입력을 다시 처리해도 원본과 합계는 증폭되지 않고, 이전 정상 결과와 이미 게시된 비교 후보도 남는다. 화면의 `처리 이력`은 복구된 실행을 `중단 복구`로 구별하고 현재 숫자는 마지막으로 게시된 정상 결과만 읽는다.

이 검증은 Python이 잡은 예외와 별도로 **로컬 처리 프로세스가 종료된 경우**를 다룬다. 현재 DuckDB 1.4.4에서는 살아 있는 별도 writer가 DB 파일을 잡는 동안 다른 프로세스의 read-only 연결도 실패할 수 있으므로, 처리 중에도 별도 UI 프로세스가 계속 읽을 수 있다고 약속하지 않는다. 프로세스 종료로 DuckDB와 `flock` 잠금이 풀린 뒤에는 마지막 정상 결과를 다시 읽고 복구할 수 있다. OS·전원 종료, 디스크 flush 손실, 파일시스템 고장까지의 내구성을 증명한 것은 아니다.

UCI의 공식 변수 설명은 `InvoiceNo`가 `C`로 시작하면 cancellation이라고 설명한다. 현재 구현은 [공식 데이터셋 설명](https://archive.ics.uci.edu/dataset/352/online+retail)에 근거해, 원문 `InvoiceNo`를 바꾸지 않고 관찰용 표시만 만든다.

- 숫자형 청구번호는 텍스트와 함께 `integer`, 문자형은 `text`라는 원본 셀 종류를 보존한다. 그 밖의 지원하지 않는 셀 타입은 원문 텍스트를 지우지 않되 표시를 `unknown`으로 둔다.
- 이 표시를 계산할 때만 앞뒤의 ASCII space·tab·CR·LF를 제거하고 첫 문자를 대소문자 구분 없이 `C`와 비교한다. 다른 Unicode 공백까지 제거한다고 약속하지 않는다.
- 원문이 없거나 지원 범위의 공백뿐이거나 셀 타입을 지원하지 않으면 `unknown`, C/c 접두가 보이면 `true`, 그 밖은 `false`다.
- `false`는 판매 확정이 아니다. 이 표시는 원판매 연결·환불·결제·할인 분류를 뜻하지 않는다.
- 과거 결과 JSON에 표시나 변환 지문이 없으면 화면은 이를 미계산/미확인으로 읽는다.

첫 실제 원본에서 확인할 대표 결과는 다음과 같다.

| 원문 날짜 | 상품 코드 | 표본 수량 합계 | 관찰 행 수 | 원천 취소 표시 | 기여 원본 |
|---|---:|---:|---:|---|---|
| 2010-12-01 | `85123A` | 6 | 1 | `false`—판매 확정 아님 | Excel 2행 |
| 2010-12-01 | `D` | -1 | 1 | `true`—원판매·환불 연결 미확인 | Excel 143행 |

`data 없음`, 결과 파일 `읽기 실패`, 실제 값 `0`은 서로 다른 상태로 취급한다. 상품 필터에 결과가 없으면 `0`이라고 표시하지 않는다.

## 검증

```bash
pytest -q
```

검사는 다음 주장을 서로 다른 근거로 확인한다.

- 보존 원본 SHA와 manifest가 일치한다.
- DuckDB SQL 결과가 openpyxl만 사용한 독립 Python 합계와 일치한다.
- 같은 입력을 다시 처리해도 원본 행 수와 상품 결과가 불어나지 않는다.
- 같은 원본에서 `[2,3] → [3] → [3,2]`로 범위를 바꿔도 각 실행의 결과·기여 행만 계산되고, 같은 행 집합은 같은 범위 지문을 가진다.
- 실패한 새 범위는 현재 정상 결과나 비교 가능한 성공 결과 목록에 들어가지 않는다.
- 비교 화면은 선택 범위 차이와 파일·시트·SQL 기준 차이를 구별한다.
- 숫자형 InvoiceNo는 원문 문자열과 `integer` 종류를 보존한 채 `false`, C/c 접두는 `true`, 누락·공백·지원하지 않는 셀 타입은 `unknown`으로 관찰하고 표본 합계를 바꾸지 않는다.
- 원천 변환 SQL과 집계 SQL의 지문을 따로 남기며, 이전 JSON에 원천 표시가 없으면 미계산/미확인으로 처리한다.
- 내용이 같아도 원본 위치가 다른 두 행은 업무 중복으로 추정해 지우지 않고 둘 다 합산한다.
- 상품 하나를 고르면 다른 상품 행이 기여 행에 섞이지 않는다.
- 정수로 손실 없이 표현할 수 없는 수량·읽기 실패·DB 적재 뒤 실패·JSON 작성 뒤 실패가 마지막 정상 표시를 덮지 않는다.
- 준비 신호를 낸 별도 프로세스를 DB commit·artifact·게시 경계에서 실제로 강제 종료해 rollback, 중단 이력 복구, 미게시 artifact 격리, 게시 성공 유지가 DB 증거와 일치한다.
- 살아 있는 writer가 잠금을 가진 동안 두 번째 writer가 실행되지 않고, 시간 추정으로 첫 실행을 실패 처리하지 않는다.
- 상세 단가는 임의의 소수 자릿수로 반올림하거나 자르지 않고 원본에서 읽은 문자열을 보여 준다. 금액 계산·반올림 규칙은 아직 정하지 않았다.
- 수량 `0`이 있는 행과 해당 상품 행이 아예 없는 상태를 구별한다.
- 생성 JSON과 화면 데이터에 `CustomerID`가 포함되지 않는다.

## 아직 구현하지 않은 것

- UCI 전체 파일의 품질 조사와 데이터셋 최종 채택
- 판매·취소 원거래 연결, 중복 거래 판별, 영업일·시간대, 금액 반올림 계약
- dbt·Airflow·PostgreSQL 채택
- Kafka producer/consumer와 중복·역순·offset commit 전 중단 복구
- Spark의 통제된 규모·분포·실행 계획 비교
- 실제 운영 DB, 클라우드, 배포

다음 도구는 이름 순서로 붙이지 않는다. 현재 DuckDB 배치 결과가 Kafka 결과를 대조할 기준이 되며, Kafka 단계에서는 실제 broker·producer·consumer를 실행해 중단과 재개 뒤 최종 저장 결과를 비교한다. PostgreSQL은 다중 프로세스 쓰기와 서비스 제공 경계가 필요할 때, Spark는 같은 결과의 규모 비교가 필요할 때 연다.

## 데이터 출처와 공개 범위

**원천:** Chen, D. (2015). *Online Retail* [Dataset]. UCI Machine Learning Repository. [DOI: 10.24432/C5BW33](https://doi.org/10.24432/C5BW33). [공식 데이터 설명](https://archive.ics.uci.edu/dataset/352/online+retail)은 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)을 명시한다. 라이선스의 보증 부인 등 조건은 링크한 원문을 따른다.

공개 코드에는 원본 Excel·로컬 DB·생성 JSON·고객 식별값·개인 실행 경로를 포함하지 않는다. 테스트의 `ROWS`는 공개 원천 일부를 바탕으로 고객 식별자를 가상 값 `TEST-CUSTOMER`로 교체하고 0·누락·잘못된 타입·공백 등 반례를 추가한 가공 자료다. 해당 원천에서 유래한 데이터에는 위 출처와 CC BY 4.0이 적용되며 실제 전체 원천 또는 Kafka 사건 자료로 보지 않는다. 로컬에서 별도로 받은 원본과 DB에는 원천 `CustomerID`가 남을 수 있으나 결과 JSON·화면에는 제공하지 않는다.

이 게시본은 원본 추적·선택 범위 재처리·원천 취소 표시·프로세스 중단 복구까지 검증한 로컬 구현이다. Kafka 진행 코드와 실행파일은 이번 게시본에 포함하지 않으며 완료 경험으로 주장하지 않는다. 공개 웹 서비스나 운영 환경 배포도 아니다.

프로젝트 코드의 재사용 라이선스는 아직 지정하지 않았다. 데이터의 CC BY 4.0을 코드 전체의 라이선스로 해석하지 않는다.
