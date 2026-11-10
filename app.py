# -*- coding: utf-8 -*-
"""강원 고령인구 폭염 취약성 Web-GIS 공개 지도 (기본틀).

실행: python -m streamlit run app.py
데이터 출처: 사용자 제공 2025년 2분기 강원 18개 시군 경계 (SGIS).
"""
from pathlib import Path
from io import BytesIO
import json
import re
import pandas as pd
import folium
import streamlit as st
from branca.colormap import linear
from streamlit_folium import st_folium

st.set_page_config(page_title="강원 고령인구 폭염 취약성 Web-GIS", page_icon="🗺️", layout="wide")
ROOT = Path(__file__).resolve().parent
GEO_PATH = ROOT / "data" / "gangwon_18.geojson"
REGIONS = ['강릉시','고성군','동해시','삼척시','속초시','양구군','양양군','영월군','원주시','인제군','정선군','철원군','춘천시','태백시','평창군','홍천군','화천군','횡성군']
SCENARIOS = ['SSP1-2.6','SSP2-4.5','SSP3-7.0','SSP5-8.5']


def clean_region(s):
    return re.sub(r'\s+', '', str(s).replace('강원특별자치도','').replace('강원도','').strip())


def pick_index(columns, tokens):
    return next((i for i, c in enumerate(columns) if any(t in str(c).lower() for t in tokens)), 0)


def numeric_columns(df):
    return [c for c in df.columns if pd.to_numeric(df[c], errors='coerce').notna().any()]


@st.cache_data(show_spinner=False)
def load_boundary():
    with open(GEO_PATH, encoding='utf-8') as f:
        obj = json.load(f)
    features = []
    for feature in obj['features']:
        p = feature['properties']
        name = clean_region(p.get('SIGUNGU_NM', ''))
        if name in REGIONS:
            features.append({'type': 'Feature', 'properties': {'region': name, 'code': str(p.get('SIGUNGU_CD',''))}, 'geometry': feature['geometry']})
    return {'type': 'FeatureCollection', 'features': features}


st.title('강원지역 고령인구 폭염 취약성 Web-GIS')
st.caption('기후변화 시나리오별 공간분석 및 미래 전망지도 | 연구용 공개 기본틀')
st.info('현재는 **행정구역 지도와 분석조건 선택 기능을 시연하는 버전**입니다. HW33·TR25 수치는 CSV 업로드 후 표시되며, 취약성 지수 및 A·B안 계산 결과는 아직 연결되지 않았습니다.')

with st.sidebar:
    st.header('분석 조건')
    scenario = st.selectbox('기후변화 시나리오', SCENARIOS)
    decade = st.selectbox('분석 연대', [f'{y}년대' for y in range(2020,2100,10)])
    metric = st.selectbox('표시 항목', ['행정구역 기본틀','HW33','TR25','폭염 취약성 지수 (준비 중)'])
    weight = st.selectbox('취약성 지수 가중치', ['A안: E 0.50 / S 0.25 / AC 0.25','B안: 자체 가중치 (산식 확정 후 적용)'])
    st.divider()
    st.subheader('기후자료 연결 (선택)')
    uploaded = st.file_uploader('가공된 HW33 또는 TR25 CSV 업로드', type=['csv'])
    st.caption('행정구역 기본틀은 파일 없이 바로 표시됩니다. CSV를 업로드하면 열 구조에 맞춰 선택합니다.')

geo = load_boundary()
if len(geo['features']) != 18:
    st.error(f'지도 경계 개수가 18개가 아닙니다: {len(geo["features"])}개. 파일을 확인하세요.')
    st.stop()

lookup = {}
value_name = '지표값'
if metric in ('HW33','TR25') and uploaded is not None:
    raw = uploaded.getvalue()
    df = None
    for enc in ('utf-8-sig','cp949','euc-kr'):
        try:
            df = pd.read_csv(BytesIO(raw),encoding=enc)
            break
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            st.error(f'CSV 읽기 오류: {exc}')
            st.stop()
    if df is None or df.empty:
        st.error('CSV 파일을 읽을 수 없거나 데이터가 없습니다.')
        st.stop()
    columns = list(df.columns)
    with st.sidebar:
        st.subheader('CSV 열 지정')
        region_col = st.selectbox('시군 이름 열',columns,index=pick_index(columns,['시군구','시군','지역명','region']))
        choices = ['(선택 안 함)'] + columns
        scenario_col = st.selectbox('SSP 열',choices,index=(1+pick_index(columns,['scenario','시나리오','ssp']) if any(any(t in str(c).lower() for t in ['scenario','시나리오','ssp']) for c in columns) else 0))
        decade_col = st.selectbox('연대 열',choices,index=(1+pick_index(columns,['decade','연대']) if any(any(t in str(c).lower() for t in ['decade','연대']) for c in columns) else 0))
        nums = numeric_columns(df)
        value_col = st.selectbox('실제 지표값 열', nums) if nums else None
    if not value_col:
        st.warning('숫자로 해석할 수 있는 열을 찾지 못했습니다.')
    else:
        d = df.copy()
        d['_region'] = d[region_col].map(clean_region)
        if scenario_col != '(선택 안 함)':
            d = d[d[scenario_col].astype(str).str.upper().str.replace(r'[^0-9A-Z]','',regex=True).eq(re.sub(r'[^0-9A-Z]','',scenario.upper()))]
        else:
            st.warning('SSP 시나리오 열을 지정하지 않아 시나리오 필터가 적용되지 않습니다.')
        if decade_col != '(선택 안 함)':
            d = d[d[decade_col].astype(str).str.contains(decade[:4],regex=False,na=False)]
        else:
            st.warning('연대 열을 지정하지 않아 연대 필터가 적용되지 않습니다.')
        d['_value'] = pd.to_numeric(d[value_col], errors='coerce')
        d = d[d['_region'].isin(REGIONS) & d['_value'].notna()]
        duplicates = d['_region'].duplicated(keep=False)
        if duplicates.any():
            st.warning('같은 선택 조건에서 중복된 시군이 있습니다. 중복 시군 값은 임의 평균하지 않고 제외합니다.')
            d = d[~duplicates]
        lookup = dict(zip(d['_region'],d['_value']))
        value_name = str(value_col)

if metric == '폭염 취약성 지수 (준비 중)':
    st.warning('취약성 지수 데이터가 아직 연결되지 않았습니다. 가중치 선택은 화면 구조 확인용입니다.')
elif metric in ('HW33','TR25') and not lookup:
    st.info('분석값은 아직 표시되지 않습니다. 왼쪽에 해당 지표 CSV를 업로드하고 열을 지정하세요.')

with st.container():
    c1,c2,c3,c4 = st.columns(4)
    c1.metric('대상 행정구역', '18개 시군')
    c2.metric('SSP 시나리오', scenario)
    c3.metric('분석 연대', decade)
    c4.metric('지표 연결', f'{len(lookup)}/18개')

# 시군 경계 전체를 포함하는 고정 지도 범위. 좌표는 경계 자료의 대략적인 외접 범위.
m = folium.Map(location=[37.73,128.26], zoom_start=8, tiles='CartoDB positron', control_scale=True)
vals = list(lookup.values())
cmap = None
if vals:
    lo,hi = min(vals),max(vals)
    cmap = linear.YlOrRd_09.scale(lo, hi if hi > lo else lo+1e-9)
    cmap.caption = value_name

for f in geo['features']:
    name = f['properties']['region']
    val = lookup.get(name)
    f['properties']['display_value'] = f'{val:.4g}' if val is not None else '데이터 미연결'
    f['properties']['value'] = float(val) if val is not None else None

def style(feature):
    v = feature['properties'].get('value')
    return {'fillColor': cmap(v) if (cmap is not None and v is not None) else '#d9e4ee',
            'color':'#526474','weight':1.5,'fillOpacity':0.75}

folium.GeoJson(geo,name='강원 18개 시군',style_function=style,
    highlight_function=lambda feature: {'weight':3,'color':'#1f2937','fillOpacity':0.9},
    tooltip=folium.GeoJsonTooltip(fields=['region','display_value'],aliases=['시군: ', f'{value_name}: ']),
    popup=folium.GeoJsonPopup(fields=['region','code','display_value'],aliases=['시군: ','행정구역 코드: ',f'{value_name}: '])
).add_to(m)
if cmap is not None:
    cmap.add_to(m)
folium.LayerControl(collapsed=True).add_to(m)
st_folium(m, height=600, use_container_width=True, returned_objects=[])

st.subheader('시군별 자료')
table = pd.DataFrame({'시군':REGIONS, value_name:[lookup.get(region) for region in REGIONS]})
if lookup:
    table = table.sort_values(value_name, ascending=False, na_position='last')
st.dataframe(table, hide_index=True, use_container_width=True)
if lookup:
    st.download_button('선택한 시군별 자료 CSV 다운로드',table.to_csv(index=False).encode('utf-8-sig'),file_name='gangwon_selected_data.csv',mime='text/csv')
else:
    st.caption('시군별 수치가 연결되기 전에는 임의의 폭염 또는 취약성 값을 채우지 않습니다.')

st.divider()
st.caption('경계자료: 사용자 제공 SGIS 시군구 경계 2025년 2분기 (기준일 2025-06-30). 공개 기본틀은 데이터 미연결 상태에서도 지도를 보여줍니다.')
