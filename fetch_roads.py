#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水头镇真实路网抓取：OpenStreetMap(Overpass) -> data/roads.json
输出真实道路折线(polyline, 经纬度) + 按道路等级估算车速/状态/限速。

真实数据源说明：
  - 形状（路网走向）：来自 OpenStreetMap，100% 真实，无需任何 Key。
  - 实时车速：默认按道路等级(高速/主干/次干/支路)估算并随机化，制造畅通/缓行/拥堵差异；
    若设置环境变量 AMAP_KEY（高德 Web 服务 Key），则逐条调用高德「交通态势」接口，
    用其返回的真实 speed + 道路折线覆盖（同一份 roads.json 即含真实形状 + 真实车速）。
运行：python fetch_roads.py   依赖：pip install requests
"""
import json
import os
import math
import time
import random
import sys
import traceback

try:
    import requests
except ImportError:
    requests = None

OVERPASS = "https://overpass-api.de/api/interpreter"
# 水头镇包围盒：经核实镇中心约 120.40E, 27.62N（北港片区）
BBOX = (27.55, 120.33, 27.68, 120.48)  # south, west, north, east
CENTER = ((BBOX[1] + BBOX[3]) / 2.0, (BBOX[0] + BBOX[2]) / 2.0)

# 道路等级 -> (默认限速 km/h, 是否主干)
HW = {
    'motorway': (80, True), 'trunk': (70, True), 'primary': (60, True),
    'secondary': (50, True), 'tertiary': (40, False), 'residential': (30, False),
    'unclassified': (30, False), 'service': (20, False), 'living_street': (20, False),
}
MAJOR = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary'}


def haversine(a, b):
    R = 6371000.0
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def decimate(geom, step_m=30):
    """抽稀几何点：间距小于 step_m 的合并，保留首尾。OSM 原始点过密。"""
    pts = [(p['lon'], p['lat']) for p in geom]
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    last = pts[0]
    for p in pts[1:-1]:
        if haversine(last, p) >= step_m:
            out.append(p)
            last = p
    out.append(pts[-1])
    return out


def status_of(speed, limit):
    if limit <= 0:
        return 'smooth'
    ratio = speed / limit
    if ratio >= 0.6:
        return 'smooth'
    if ratio >= 0.4:
        return 'slow'
    return 'congested'


def query_overpass():
    s, w, n, e = BBOX
    q = "[out:json][timeout:90];(way[\"highway\"](%s,%s,%s,%s););out geom;" % (s, w, n, e)
    r = requests.get(OVERPASS, params={'data': q}, timeout=90)
    r.raise_for_status()
    return r.json()


def enrich_with_amap(roads, key):
    """可选：用高德交通态势接口覆盖 speed/status，并采用高德返回的真实折线。"""
    city = "平阳县"
    for rd in roads:
        name = rd.get('name')
        if not name:
            continue
        try:
            url = "https://restapi.amap.com/v3/traffic/status/road"
            params = {'key': key, 'name': name, 'city': city, 'extensions': 'all'}
            resp = requests.get(url, params=params, timeout=10)
            j = resp.json()
            if j.get('status') != '1' or 'trafficinfo' not in j:
                continue
            info = j['trafficinfo']
            roads_info = info.get('roads') or []
            if not roads_info:
                continue
            ri = roads_info[0]
            sp = int(ri.get('speed') or rd['speed'])
            st = ri.get('status')  # 0 畅通 1 缓行 2 拥堵
            status = {'0': 'smooth', '1': 'slow', '2': 'congested'}.get(str(st), rd['status'])
            poly = ri.get('polyline')
            if poly:
                rd['polyline'] = [[float(c.split(',')[0]), float(c.split(',')[1])]
                                  for c in poly.split(';') if ',' in c]
            rd['speed'] = sp
            rd['status'] = status
        except Exception as ex:
            print("[amap skip]", name, str(ex)[:80])


def main():
    roads = []
    if requests is None:
        print("[warn] requests 未安装，输出空结构")
    else:
        try:
            data = query_overpass()
        except Exception as ex:
            print("[overpass err]", str(ex)[:160])
            data = {'elements': []}
        for el in data.get('elements', []):
            if el.get('type') != 'way':
                continue
            tags = el.get('tags', {})
            cls = tags.get('highway')
            if cls not in HW:
                continue
            geom = el.get('geometry')
            if not geom or len(geom) < 2:
                continue
            name = tags.get('name') or tags.get('name:zh') or ''
            pts = decimate(geom)
            if len(pts) < 2:
                continue
            length = sum(haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
            if length < 80:  # 丢弃极短的巷子，避免噪声
                continue
            limit, _ = HW[cls]
            # 估算自由流车速（限速的 55%~95%），制造差异
            speed = max(8, int(limit * (0.55 + 0.4 * random.random())))
            roads.append({
                'id': 'osm_' + str(el.get('id')),
                'name': name or ('%s_%s' % (cls, el.get('id'))),
                'cls': cls,
                'polyline': [[round(lon, 6), round(lat, 6)] for lon, lat in pts],
                'speed': speed,
                'status': status_of(speed, limit),
                'limit': limit,
                'lengthM': int(length),
                'major': cls in MAJOR,
            })

    # 可选：高德真实车速覆盖
    amap_key = os.environ.get('AMAP_KEY')
    if amap_key and roads:
        print("[amap] 使用高德交通态势覆盖车速/折线")
        enrich_with_amap(roads, amap_key)

    # 排序：主干优先、限速高优先
    roads.sort(key=lambda r: (0 if r['major'] else 1, -r['limit']))

    out = {
        'updatedAt': time.strftime('%Y-%m-%d %H:%M', time.localtime()),
        'center': [round(CENTER[0], 6), round(CENTER[1], 6)],
        'source': 'OpenStreetMap(Overpass)' + (' + 高德交通态势' if amap_key else ''),
        'roads': roads,
    }
    os.makedirs('data', exist_ok=True)
    with open('data/roads.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('[done] roads =', len(roads))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
