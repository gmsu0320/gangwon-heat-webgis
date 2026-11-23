# -*- coding: utf-8 -*-
"""강원 18개 시군 폭염 취약성 Web-GIS (기존 기본틀 보존 + 최종 HVI 연동).

실행: python -m streamlit run app.py
지도 경계: 사용자가 확보한 2025년 2분기 SGIS GeoJSON (data/gangwon_18.geojson)
분석자료: 최신 방법 3 인구전망을 적용한 A안 HVI 576행
"""
from pathlib import Path
from html import escape
import json
import re
import pandas as pd
import altair as alt
import folium
import streamlit as st
from branca.colormap import linear
from branca.element import Element, MacroElement, Template
from streamlit_folium import st_folium

st.set_page_config(page_title="강원 고령인구 폭염 취약성 Web-GIS", page_icon="🗺️", layout="wide")
ROOT = Path(__file__).resolve().parent
GEO_PATH = ROOT / "data" / "gangwon_18.geojson"
HVI_PATH = ROOT / "data" / "04_최종HVI_576행.csv"
REGIONS = ['강릉시','고성군','동해시','삼척시','속초시','양구군','양양군','영월군',
           '원주시','인제군','정선군','철원군','춘천시','태백시','평창군','홍천군','화천군','횡성군']
SCENARIOS = ['SSP1-2.6','SSP2-4.5','SSP3-7.0','SSP5-8.5']
# 그래프 선 색상 고정: SSP1 진파랑 / SSP2 연파랑 / SSP3 연분홍 / SSP5 빨강
SSP_LINE_COLORS = ['#0877D9', '#74C6FF', '#F5A1A8', '#FF263B']
DECADES = {f'{year}년대': str(year) for year in range(2020, 2100, 10)}
METRICS = {
    '종합 폭염 취약성 (HVI)': 'HVI_A',
    '기후 노출도 (E)': '기후노출_E',
    '민감도 (S)': '민감도_S',
    '적응능력 (AC)': '적응능력_AC',
    'HW33 (폭염일수)': 'HW33_일수',
    'TR25 (열대야일수)': 'TR25_일수',
    '65세 이상 인구 비율': 'R65_pct',
    '75세 이상 구성비': 'R75in65_pct',
    '총인구': '총인구_평균_명',
    '65세 이상 인구': '65세이상_평균_명',
    '75세 이상 인구': '75세이상_평균_명',
}
NORMALIZED = {'HVI_A','기후노출_E','민감도_S','적응능력_AC'}
PERCENT = {'R65_pct','R75in65_pct'}
POPULATION = {'총인구_평균_명','65세이상_평균_명','75세이상_평균_명'}
DAYS = {'HW33_일수','TR25_일수'}


def clean_region(value):
    return re.sub(r'\s+', '', str(value).replace('강원특별자치도', '')
                  .replace('강원도', '').strip())


def clean_decade(value):
    hit = re.search(r'20[2-9]0', str(value))
    return hit.group() if hit else ''


def format_value(value, col):
    if pd.isna(value):
        return '자료 없음'
    value = float(value)
    if col in POPULATION:
        return f'{value:,.0f}명'
    if col in PERCENT:
        return f'{value:.1f}%'
    if col in DAYS:
        return f'{value:.1f}일'
    return f'{value:.4f}'


def _point_in_ring(lon, lat, ring):
    """레이캐스팅으로 경위도가 GeoJSON 선형 링 내부에 있는지 판별."""
    inside = False
    if len(ring) < 3:
        return False
    for idx in range(len(ring)):
        x1, y1 = ring[idx][:2]
        x2, y2 = ring[(idx + 1) % len(ring)][:2]
        if (y1 > lat) != (y2 > lat):
            x_at_lat = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at_lat:
                inside = not inside
    return inside


def _point_in_geometry(lon, lat, geometry):
    """Polygon / MultiPolygon 및 내부 구멍(hole)을 지원."""
    kind = geometry.get('type')
    if kind == 'Polygon':
        polygons = [geometry.get('coordinates', [])]
    elif kind == 'MultiPolygon':
        polygons = geometry.get('coordinates', [])
    else:
        return False

    for polygon in polygons:
        if not polygon:
            continue
        outer_ring, *holes = polygon
        if _point_in_ring(lon, lat, outer_ring) and not any(
            _point_in_ring(lon, lat, hole) for hole in holes
        ):
            return True
    return False


def region_from_map_click(click, geojson):
    """Folium에서 전달한 클릭 좌표를 18개 시군 GeoJSON에 연결."""
    point = click.get('last_object_clicked')
    if isinstance(point, dict):
        try:
            lat = float(point['lat'])
            lon = float(point['lng'])
        except (KeyError, ValueError, TypeError):
            pass
        else:
            for feature in geojson['features']:
                if _point_in_geometry(lon, lat, feature['geometry']):
                    return feature['properties']['region']

    # 구버전 호환용: 좌표가 전달되지 않을 때 툴팁의 시군 이름 사용
    tooltip = click.get('last_object_clicked_tooltip')
    if isinstance(tooltip, str):
        return next((name for name in REGIONS if name in tooltip), None)
    return None


def map_click_token(click):
    """같은 클릭 이벤트에 의한 무한 재실행 및 선택값 되돌림 방지."""
    click_count = click.get('last_object_clicked_count')
    try:
        if click_count is not None and int(click_count) > 0:
            return ('count', int(click_count))
    except (TypeError, ValueError):
        pass

    # 예전 streamlit-folium에는 클릭 횟수 반환값이 없을 수 있음
    point = click.get('last_object_clicked')
    if isinstance(point, dict) and 'lat' in point and 'lng' in point:
        try:
            return ('coordinate', round(float(point['lat']), 8),
                    round(float(point['lng']), 8))
        except (TypeError, ValueError):
            return None
    return None


@st.cache_data(show_spinner=False)
def load_boundary():
    with GEO_PATH.open(encoding='utf-8') as f:
        obj = json.load(f)
    features = []
    for feature in obj.get('features', []):
        properties = feature.get('properties', {})
        name = clean_region(properties.get('SIGUNGU_NM', ''))
        if name in REGIONS:
            features.append({
                'type': 'Feature',
                'properties': {'region': name, 'code': str(properties.get('SIGUNGU_CD', ''))},
                'geometry': feature['geometry']
            })
    if len(features) != 18 or set(f['properties']['region'] for f in features) != set(REGIONS):
        raise ValueError(f'SGIS 행정경계 18개 확인 실패: {len(features)}개. '
                         'SIGUNGU_NM 열과 GeoJSON 파일을 점검하세요.')
    return {'type': 'FeatureCollection', 'features': features}


@st.cache_data(show_spinner=False)
def load_hvi():
    try:
        df = pd.read_csv(HVI_PATH, encoding='utf-8-sig')
    except UnicodeDecodeError:
        df = pd.read_csv(HVI_PATH, encoding='cp949')
    needed = ['시군','SSP','연대','취약성순위'] + list(METRICS.values())
    absent = [c for c in needed if c not in df.columns]
    if absent:
        raise ValueError('CSV에서 필요한 열을 찾지 못했습니다: ' + ', '.join(absent))
    df['시군'] = df['시군'].map(clean_region)
    df['연대키'] = df['연대'].map(clean_decade)
    for col in ['취약성순위'] + list(METRICS.values()):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    if df[needed[3:]].isna().any().any():
        raise ValueError('분석 지표에 결측 또는 비숫자 값이 있습니다.')
    keys = ['시군','SSP','연대키']
    if df.duplicated(keys).any():
        raise ValueError('시군·SSP·연대 조합이 중복됩니다.')
    expected = {(r, s, d) for r in REGIONS for s in SCENARIOS for d in DECADES.values()}
    actual = set(map(tuple, df[keys].itertuples(index=False, name=None)))
    if len(df) != 576 or actual != expected:
        raise ValueError(f'분석값 조합 오류: {len(df)}행. '
                         f'누락 {len(expected - actual)}건, 불필요 {len(actual - expected)}건')
    return df


# 설명 팝업: Streamlit 1.34 이상의 st.dialog 사용
@st.dialog('폭염 취약성(HVI)이란?')
def show_hvi_dialog():
    st.markdown("""
**폭염 취약성 지수(HVI)는** 지역별 폭염 위험, 인구 특성, 대응 여건을
종합하여 **폭염에 상대적으로 얼마나 취약한지** 나타내는 지수입니다.
**값이 높을수록 폭염 취약성이 높습니다.**

**① 기후 노출도(E)** — 얼마나 더운 날이 많은가?
- **HW33:** 일 최고기온 33℃ 이상인 날의 수(폭염일수)
- **TR25:** 일 최저기온 25℃ 이상인 날의 수(열대야일수)

**② 민감도(S)** — 폭염에 영향을 받기 쉬운 특성이 얼마나 큰가?
- 65세 이상 인구 비율, 65세 이상 중 75세 이상 비율
- 독거노인가구 비율, 온열질환 발생률

**③ 적응능력(AC)** — 폭염에 대응할 여건이 얼마나 갖춰져 있는가?
- 65세 이상 1,000명당 무더위쉼터 수
- 전체 인구 10만 명당 응급의료기관 수
- **AC가 높을수록 대응능력이 좋은 것**으로 해석합니다.

**최종 계산식**

`HVI = 0.50 × E + 0.25 × S + 0.25 × (1 − AC)`

세 영역은 0~1 범위로 정규화하여 계산합니다.
**HVI 0.7은 피해 발생 확률이 70%라는 의미가 아닌, 지역 간 상대 비교값**입니다.
    """)


@st.dialog('SSP 시나리오란?')
def show_ssp_dialog():
    st.markdown('''
**SSP(Shared Socioeconomic Pathways)는** 미래 사회·경제 발전경로를 가정한
**공통사회경제경로**입니다. 기후변화 전망에서는 SSP 경로와 온실가스 배출·복사강제력 수준을 함께 사용합니다.

| 기후 시나리오 | 간단한 설명 |
|---|---|
| **SSP1-2.6** | 지속가능성을 중시하고 온실가스 배출이 비교적 낮은 경로 |
| **SSP2-4.5** | 현재와 비슷한 중간 수준의 사회·경제 발전경로 |
| **SSP3-7.0** | 지역 간 경쟁과 높은 배출이 지속되는 경로 |
| **SSP5-8.5** | 화석연료 중심의 고배출 경로 |

**뒤의 숫자(2.6, 4.5, 7.0, 8.5)는** 2100년 무렵의 복사강제력 수준(W/m²)을 나타냅니다.

본 지도는 네 가지 **기후** SSP를 비교합니다. 반면 미래 **인구구조**는
SSP2 국가 인구전망을 공통으로 적용해 기후 시나리오 변화에 따른 차이를 비교합니다.
    ''')


@st.dialog('분석 내용 설명')
def show_analysis_dialog():
    st.markdown('''
**연구 대상 및 기간**
- 강원특별자치도 18개 시군
- 기후변화 시나리오 4종, 2020~2090년대(10년 단위)

**폭염 취약성 지수(HVI) 산정**
- **노출도(E)**: 폭염일수(HW33), 열대야일수(TR25)
- **민감도(S)**: 65세 이상 인구 비율, 65세 이상 중 75세 이상 비율,
  독거노인가구 비율, 온열질환 발생률
- **적응능력(AC)**: 무더위쉼터와 응급의료기관의 인구 대비 공급

**HVI = 0.50 × E + 0.25 × S + 0.25 × (1 − AC)**

**미래 인구 추정 방법**
- 2025년 실제 인구를 기준으로 2026~2042년 **공식 시군별 추계**를 연결
- 2043년 이후에는 **IIASA V15 SSP2 전국 인구전망**과 **로짓 기반 인구구조 보정**을 적용
- 이는 공식 시군별 2090년대 인구전망이 아닌 **본 연구의 조건부 추정값**
- 시설 **개소 수는 2025년 수준으로 고정**하되, 인구당 공급률은 미래 인구 변화에 따라 달라짐

**해석 시 유의사항**
- 2020년대 **사회경제 지표는 2025~2029년 평균**, 기후지표의 2020년대 기간과 다름
- 온열질환 발생률은 2019~2024년 기준값을 유지
- 네 가지 기후 SSP에 **동일한 SSP2 사회경제·인구 전망**을 적용
- HVI는 상대적 취약성 지수로 **실제 건강피해 인원을 의미하지 않음**
    ''')


# 앱 시작: 잘못된 데이터로 지도에 임의의 색상을 표시하지 않도록 입력을 먼저 검증
st.title('강원지역 고령인구 폭염 취약성 Web-GIS')
if not GEO_PATH.exists():
    st.error(f'기존 행정경계 파일을 찾지 못했습니다: {GEO_PATH}')
    st.stop()
if not HVI_PATH.exists():
    st.error(f'최종 HVI 파일을 찾지 못했습니다: {HVI_PATH}')
    st.stop()
try:
    geo = load_boundary()
    df = load_hvi()
except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
    st.error(f'지도 입력자료 검증 실패: {exc}')
    st.stop()


# 지도 클릭 결과는 다음 실행에서 selectbox가 만들어지기 전에 적용해야 함.
# 이미 만들어진 위젯의 session_state를 같은 실행 중 수정하면 오류가 발생함.
if 'selected_region_widget' not in st.session_state:
    st.session_state['selected_region_widget'] = REGIONS[0]
if '_pending_map_region' in st.session_state:
    st.session_state['selected_region_widget'] = st.session_state.pop('_pending_map_region')

with st.sidebar:
    st.header('분석 조건')
    scenario = st.selectbox('기후변화 시나리오', SCENARIOS, index=3)
    decade = st.selectbox('분석 연대', list(DECADES), index=7)
    metric = st.selectbox('표시 항목', list(METRICS))
    st.divider()
    region_selected = st.selectbox('지역별 상세 조회', REGIONS, key='selected_region_widget')
    st.divider()
    st.subheader('지도 이용 안내')
    if st.button('폭염 취약성(HVI)이란?', use_container_width=True, key='hvi_help'):
        show_hvi_dialog()
    if st.button('SSP 시나리오란?', use_container_width=True, key='ssp_help'):
        show_ssp_dialog()
    if st.button('분석 내용 설명', use_container_width=True, key='analysis_help'):
        show_analysis_dialog()

metric_col = METRICS[metric]
subset = df[(df['SSP'] == scenario) & (df['연대키'] == DECADES[decade])].copy()
if len(subset) != 18:
    st.error('선택한 시나리오·연대에 해당하는 18개 시군 자료가 모두 있지 않습니다.')
    st.stop()
lookup = subset.set_index('시군').to_dict('index')

c1, c2, c3, c4 = st.columns(4)
c1.metric('대상 행정구역', '18개 시군')
c2.metric('기후 시나리오', scenario)
c3.metric('분석 연대', decade)
c4.metric('18개 시군 평균 HVI', f"{subset['HVI_A'].mean():.4f}")

# 동일 지표의 SSP/연대별 색상 비교가 가능하도록 범례는 전체 분석기간에 고정
if metric_col in NORMALIZED:
    lo, hi = 0.0, 1.0
else:
    lo = float(df[metric_col].min())
    hi = float(df[metric_col].max())
if hi <= lo:
    hi = lo + 1e-8
cmap_base = linear.YlGn_09 if metric_col == '적응능력_AC' else linear.YlOrRd_09
cmap = cmap_base.scale(lo, hi)
# 범례는 Folium iframe 바깥에 표시하여 색상 막대와 글이 겹치지 않게 합니다.
legend_label = f'{metric} · SSP/연대 공통 색상범위'

# 기존 지도 디자인 보존: 흰색 배경, 외부 지도 타일 없음, 행정경계만 표시
m = folium.Map(location=[37.73, 128.26], zoom_start=8, tiles=None,
               control_scale=False, zoom_control=True, prefer_canvas=True,
               attribution_control=False)
m.get_root().header.add_child(Element(
    '<style>.leaflet-container {background: #ffffff !important; border: 1px solid #333333 !important; box-sizing: border-box;}</style>'))

for f in geo['features']:
    name = f['properties']['region']
    item = lookup.get(name)
    val = item[metric_col] if item else None
    f['properties']['value'] = float(val) if val is not None else None
    f['properties']['display_value'] = format_value(val, metric_col)
    f['properties']['rank'] = str(int(item['취약성순위'])) if item else '-'
    f['properties']['hvi'] = format_value(item['HVI_A'], 'HVI_A') if item else '-'


def style(feature):
    value = feature['properties'].get('value')
    return {'fillColor': cmap(value) if value is not None else '#d9e4ee',
            'color':'#526474', 'weight':1.5, 'fillOpacity':0.90}


boundary_layer = folium.GeoJson(
    geo, name='강원 18개 시군', style_function=style,
    highlight_function=lambda feature: {'weight':3, 'color':'#1f2937', 'fillOpacity':0.9},
    tooltip=folium.GeoJsonTooltip(fields=['region','display_value'],
                                  aliases=['시군: ',f'{metric}: ']),
    popup=folium.GeoJsonPopup(fields=['region','code','display_value','hvi','rank'],
                              aliases=['시군: ','행정구역 코드: ',f'{metric}: ',
                                       'HVI: ','HVI 취약성 순위: '])
).add_to(m)
m.fit_bounds(boundary_layer.get_bounds(), padding=(24, 24))

# 이전 지도에서 사용한 초기 화면 고정·확대 이후 이동 허용 설정을 변경 없이 유지
class InitialViewportLock(MacroElement):
    def __init__(self, max_additional_steps=3):
        super().__init__()
        self._name = 'InitialViewportLock'
        self.max_additional_steps = int(max_additional_steps)
        self._template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
            var map = {{ this._parent.get_name() }};
            map.whenReady(function () {
                var baseZoom = map.getZoom();
                var homeCenter = map.getCenter();
                var homeBounds = map.getBounds();
                map.setMinZoom(baseZoom);
                map.setMaxZoom(baseZoom + {{ this.max_additional_steps }});
                map.setMaxBounds(homeBounds);
                map.options.maxBoundsViscosity = 1.0;
                function updateInteraction() {
                    if (map.getZoom() <= baseZoom + 0.001) {
                        map.panTo(homeCenter, {animate: false});
                        map.dragging.disable();
                        map.keyboard.disable();
                    } else {
                        map.dragging.enable();
                        map.keyboard.enable();
                    }
                }
                map.on('zoomend', updateInteraction);
                updateInteraction();
            });
        })();
        {% endmacro %}
        """)


m.add_child(InitialViewportLock(max_additional_steps=3))
# 색상 막대(LinearColormap)는 지도 레이어로 추가하지 않습니다.
folium.LayerControl(collapsed=True).add_to(m)

# 지도 위에 독립된 반응형 범례 표시 (지도와 범례가 겹치지 않도록 분리)
legend_colors = [cmap(lo + (hi - lo) * i / 8) for i in range(9)]
legend_gradient = 'linear-gradient(to right, ' + ', '.join(legend_colors) + ')'
legend_ticks = [lo + (hi - lo) * i / 4 for i in range(5)]
if metric_col in NORMALIZED:
    legend_tick_text = [f'{value:.2f}' for value in legend_ticks]
elif metric_col in POPULATION:
    legend_tick_text = [f'{value:,.0f}' for value in legend_ticks]
else:
    legend_tick_text = [f'{value:.1f}' for value in legend_ticks]
legend_ticks_html = ''.join(f'<span>{escape(value)}</span>' for value in legend_tick_text)
st.markdown(
    f'''<div style="max-width:420px;margin:8px 0 10px auto;padding:10px 14px;"
        >
          <div style="font-size:13px;font-weight:600;margin-bottom:7px;">
             {escape(legend_label)}</div>
          <div style="height:13px;border-radius:3px;border:1px solid #999;
             background:{legend_gradient};"></div>
          <div style="display:flex;justify-content:space-between;gap:6px;
             font-size:11px;margin-top:4px;color:#9ca3af;">
             {legend_ticks_html}</div>
        </div>''', unsafe_allow_html=True
)
# 시군 폴리곤을 클릭하면 클릭 지점과 클릭 횟수를 Streamlit으로 반환함.
# SSP/연대/표시 지표별 별도 키를 사용해 과거 클릭이 다른 조건에 재적용되는 문제 방지.
map_view_key = f"heatcare_map_{scenario}_{DECADES[decade]}_{metric_col}"
map_response = st_folium(
    m,
    height=600,
    use_container_width=True,
    key=map_view_key,
    returned_objects=[
        'last_object_clicked',
        'last_object_clicked_count',
        'last_object_clicked_tooltip',
    ],
)

# 클릭을 지도 선택값과 연동하고 그래프를 자동으로 갱신.
# 같은 클릭으로 반복 재실행되지 않도록 화면별로 마지막 이벤트를 저장함.
if isinstance(map_response, dict):
    event = map_click_token(map_response)
    if event is not None:
        last_events = st.session_state.setdefault('_processed_map_events', {})
        if last_events.get(map_view_key) != event:
            last_events[map_view_key] = event
            clicked_region = region_from_map_click(map_response, geo)
            if clicked_region in REGIONS and clicked_region != region_selected:
                # 아래 selectbox는 이미 렌더링되었으므로, 이번 실행에서 직접 값을
                # 바꾸지 않고 다음 실행의 최상단에서 적용한다.
                st.session_state['_pending_map_region'] = clicked_region
                st.rerun()


st.subheader('시군별 비교')
table = subset[['시군','취약성순위','HVI_A','HW33_일수','TR25_일수',
                'R65_pct','R75in65_pct','총인구_평균_명','65세이상_평균_명',
                '75세이상_평균_명']].sort_values('취약성순위').copy()
st.dataframe(table, hide_index=True, use_container_width=True)
st.download_button('현재 SSP·연대 데이터 CSV 다운로드',
                   data=subset.drop(columns=['연대키']).to_csv(index=False).encode('utf-8-sig'),
                   file_name=f'HeatCare_{scenario}_{DECADES[decade]}.csv',
                   mime='text/csv')

st.subheader(f'{region_selected} — SSP별 HVI 장기 변화')
trend = df[df['시군'] == region_selected].copy()
trend['연대표시'] = trend['연대키'].astype(str) + '년대'

# Altair를 사용해 X축 연대 라벨을 항상 가로(0도)로 표시합니다.
# 색상은 기존 SSP_LINE_COLORS 설정을 그대로 사용합니다.
line_chart = (
    alt.Chart(trend)
    .mark_line(strokeWidth=2.7)
    .encode(
        x=alt.X(
            '연대표시:N',
            sort=list(DECADES.keys()),
            axis=alt.Axis(
                title=None,
                labelAngle=0,       # 2020년대 ~ 2090년대를 가로로 표시
                labelOverlap=False,
                labelLimit=90,
                labelPadding=9,
            ),
        ),
        y=alt.Y('HVI_A:Q', title='폭염 취약성 지수 (HVI)', scale=alt.Scale(zero=True)),
        color=alt.Color(
            'SSP:N',
            scale=alt.Scale(domain=SCENARIOS, range=SSP_LINE_COLORS),
            legend=alt.Legend(title=None, orient='bottom'),
        ),
        tooltip=[
            alt.Tooltip('연대표시:N', title='연대'),
            alt.Tooltip('SSP:N', title='시나리오'),
            alt.Tooltip('HVI_A:Q', title='HVI', format='.4f'),
        ],
    )
    .properties(height=330)
)
st.altair_chart(line_chart, use_container_width=True)

st.divider()
st.caption('지도 엔진: Leaflet · 행정경계: SGIS (2025년 2분기, 사용자 제공). '
           'HVI는 상대적 취약성 지수이며 실제 건강피해 인원을 나타내지 않습니다. '
           '자세한 방법과 주의사항은 왼쪽 ‘분석 내용 설명’에서 확인할 수 있습니다.')
