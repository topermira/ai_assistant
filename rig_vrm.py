"""Добавляет в VRM 0.x пружинные кости длинному плащу и косичкам.

Плащ (N00_007_03 CLOTH_01/02 — ткань и подкладка) был жёстко привязан к тазу и бёдрам: при движении рук
он не гнулся, руки проходили сквозь полы. Косички висели на кости головы целиком, как палки.

Что делает скрипт:
  * 6 цепочек плаща (по 3 с каждой стороны, вдоль окружности плаща) от груди до подола, по 5 костей;
  * 2 цепочки косичек от затылка до кончиков, по 5 костей;
  * веса вершин: выше корня — как было, ниже плавно переходят на цепочки; между соседними цепочками и
    соседними костями — плавное смешивание (без резких границ, иначе ткань ломается);
  * коллайдеры: руки (толщиной с пышный рукав), корпус, таз;
  * пружинные группы VRM 0.x для плаща и косичек.
Исходные данные модели (геометрия, текстуры, остальные кости) не меняются.

Запуск: python rig.py вход.vrm выход.vrm
"""
import json
import struct
import sys

import numpy as np


def load(path):
    b = open(path, 'rb').read()
    magic, ver, length = struct.unpack('<III', b[:12])
    assert magic == 0x46546C67 and ver == 2
    jl, jt = struct.unpack('<II', b[12:20])
    J = json.loads(b[20:20 + jl])
    o = 20 + jl
    bl, bt = struct.unpack('<II', b[o:o + 8])
    BIN = bytearray(b[o + 8:o + 8 + bl])
    return J, BIN


def save(path, J, BIN):
    while len(BIN) % 4:
        BIN += b'\x00'
    J['buffers'][0]['byteLength'] = len(BIN)
    js = json.dumps(J, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    while len(js) % 4:
        js += b' '
    total = 12 + 8 + len(js) + 8 + len(BIN)
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total))
        f.write(struct.pack('<II', len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack('<II', len(BIN), 0x004E4942)); f.write(BIN)


CT = {5123: np.uint16, 5125: np.uint32, 5126: np.float32, 5121: np.uint8}
NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


def _loc(J, i):
    a = J['accessors'][i]; bv = J['bufferViews'][a['bufferView']]
    dt = np.dtype(CT[a['componentType']]); n = NC[a['type']]
    st = bv.get('byteStride', dt.itemsize * n)
    assert st == dt.itemsize * n, 'interleaved data not supported'
    return dt, n, a['count'], bv.get('byteOffset', 0) + a.get('byteOffset', 0)


def view(J, BIN, i):
    """Копия данных аксессора (менять можно; в файл — через write_back)."""
    dt, n, cnt, o = _loc(J, i)
    return np.frombuffer(bytes(BIN[o:o + cnt * n * dt.itemsize]), dtype=dt).reshape(cnt, n).copy()


def write_back(J, BIN, i, arr):
    dt, n, cnt, o = _loc(J, i)
    data = np.ascontiguousarray(arr, dtype=dt).tobytes()
    assert len(data) == cnt * n * dt.itemsize
    BIN[o:o + len(data)] = data


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


BACK_IN, BACK_R = 0.02, 0.03     # шарики спинки плаща (для кос): сдвиг внутрь и радиус
LEG_R, KNEE_R = 0.10, 0.08
CAPE_SMOOTH = 12                 # проходов сглаживания весов накидки
CAPE_SM_TOP = 1.27               # выше этой высоты веса накидки не сглаживаются       # бёдра и колени для юбки (в покое до юбки ~2 см)


def main(src, dst):
    J, BIN = load(src)
    nodes = J['nodes']
    name2i = {n['name']: i for i, n in enumerate(nodes)}
    par = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            par[c] = i

    def wpos(i):
        p = np.zeros(3)
        while i is not None:
            n = nodes[i]
            assert n.get('rotation', [0, 0, 0, 1]) == [0, 0, 0, 1] and n.get('scale', [1, 1, 1]) == [1, 1, 1], n['name']
            p += np.array(n.get('translation', [0, 0, 0]), dtype=float)
            i = par.get(i)
        return p

    def add_node(name, parent, world):
        i = len(nodes)
        local = (np.asarray(world, float) - wpos(parent)).tolist()
        nodes.append({'name': name, 'translation': [float(v) for v in local]})
        nodes[parent].setdefault('children', []).append(i)
        par[i] = parent
        return i

    def add_chain(prefix, parent, pts):
        """pts — мировые точки суставов (последняя — кончик _end). Возвращает индексы узлов."""
        ids, p = [], parent
        for k, w in enumerate(pts):
            nm = f'{prefix}_end' if k == len(pts) - 1 else f'{prefix}{k + 1}'
            p = add_node(nm, p, w)
            ids.append(p)
        return ids

    mesh_of = {n['mesh']: i for i, n in enumerate(nodes) if 'mesh' in n}
    body_mi = next(i for i, m in enumerate(J['meshes']) if any('Tops' in J['materials'][p['material']]['name'] for p in m['primitives']))
    hair_mi = next(i for i, m in enumerate(J['meshes']) if all('HAIR' in J['materials'][p['material']]['name'] for p in m['primitives']))
    body_skin = nodes[mesh_of[body_mi]]['skin']; hair_skin = nodes[mesh_of[hair_mi]]['skin']

    # ---------------- ПЛАЩ ----------------
    bm = J['meshes'][body_mi]
    coat_prims = [p for p in bm['primitives'] if J['materials'][p['material']]['name'].startswith('N00_007_03_Tops_01_CLOTH_0')
                  and not J['materials'][p['material']]['name'].startswith('N00_007_03_Tops_01_CLOTH_03')]
    assert len(coat_prims) == 2, [J['materials'][p['material']]['name'] for p in coat_prims]
    attrs = bm['primitives'][0]['attributes']
    P = view(J, BIN, attrs['POSITION']).astype(float)
    JO = view(J, BIN, attrs['JOINTS_0']); WE = view(J, BIN, attrs['WEIGHTS_0'])
    cv = np.unique(np.concatenate([view(J, BIN, p['indices']).ravel() for p in coat_prims]))
    Q = P[cv]
    th = np.degrees(np.arctan2(Q[:, 0], Q[:, 2]))      # 0 — середина спины (+z), ±90 — бока; x<0 — левая сторона

    # угловой размах плаща по высоте (у груди он заходит на бока, у подола — только сзади)
    ys = np.linspace(Q[:, 1].min(), Q[:, 1].max(), 24)
    thmax = np.array([np.abs(th[np.abs(Q[:, 1] - y) < 0.05]).max() if (np.abs(Q[:, 1] - y) < 0.05).any() else 60 for y in ys])

    def thmax_at(y):
        return np.interp(y, ys, thmax)

    U = np.array([-0.9, -0.55, -0.2, 0.2, 0.55, 0.9])     # положения цепочек в долях размаха
    PART = {0.9: 'SideA', 0.55: 'SideB', 0.2: 'Back'}
    # высоты суставов плаща (последняя — кончик). Корень у пояса, а не у груди: юбка и ноги идут от таза, и плащ,
    # висевший на груди, при наклоне корпуса уезжал относительно них — входил в юбку и торс
    HC = [1.08, 0.96, 0.84, 0.72, 0.60, 0.49, 0.38]
    chest = name2i['J_Bip_C_Hips']                              # родитель цепочек плаща — таз
    coat_chains = []
    for u in U:
        pts = []
        for y in HC:
            t = u * thmax_at(y)
            near = (np.abs(Q[:, 1] - y) < 0.06) & (np.abs(th - t) < 14)
            if near.sum() < 3:
                near = np.argsort(np.abs(Q[:, 1] - y) * 4 + np.abs(th - t) / 60)[:12]
            r = float(np.median(np.hypot(Q[near, 0], Q[near, 2]))) - 0.006
            pts.append([r * np.sin(np.radians(t)), y, r * np.cos(np.radians(t))])
        side = 'L' if u < 0 else 'R'
        ids = add_chain(f'J_Sec_{side}_CoatSkirtLong{PART[abs(u)]}', chest, pts)
        coat_chains.append(ids)

    # ---------------- КОСИЧКИ ----------------
    hm = J['meshes'][hair_mi]
    hattrs = hm['primitives'][0]['attributes']
    HP = view(J, BIN, hattrs['POSITION']).astype(float)
    HJO = view(J, BIN, hattrs['JOINTS_0']); HWE = view(J, BIN, hattrs['WEIGHTS_0'])
    HB = [1.31, 1.19, 1.07, 0.96, 0.87, 0.80]
    head = name2i['J_Bip_C_Head']
    # Косы в исходной модели висят ВНУТРИ плаща: он расходится от спины колоколом (на высоте 0.9 м — на 7 см
    # дальше от спины, чем коса). Пружины держали их там же, а шарики-препятствия с изнанки толкали
    # косу ещё глубже. Отодвигаем косы наружу, на поверхность накидки и плаща (+ 8 мм), — дальше их держат
    # шарики под тканью (см. «braid_cloth_cg»).
    cl_prims = [p_ for p_ in bm['primitives'] if J['materials'][p_['material']]['name'].startswith('N00_007_03_Tops_01_CLOTH_0')]
    CLV = P[np.unique(np.concatenate([view(J, BIN, p_['indices']).ravel() for p_ in cl_prims]))]
    braid_mask = (HP[:, 1] < 1.30) & (HP[:, 2] > 0.06)
    YG = np.arange(0.76, 1.32, 0.02)
    braid_surf = {}
    for side, sgn in (('L', -1), ('R', 1)):
        m_ = braid_mask & (HP[:, 0] * sgn > 0)
        dz = np.zeros(len(YG))
        for k, y in enumerate(YG):
            sl = m_ & (np.abs(HP[:, 1] - y) < 0.012)
            if sl.sum() < 4:
                continue
            xc = float(np.median(HP[sl, 0]))
            zf = float(np.percentile(HP[sl, 2], 10))            # передняя (к спине) сторона косы
            cs = (np.abs(CLV[:, 1] - y) < 0.015) & (np.abs(CLV[:, 0] - xc) < 0.035) & (CLV[:, 2] > 0)
            if cs.any():
                dz[k] = max(0.0, float(CLV[cs, 2].max()) + 0.008 - zf)
        dz = np.maximum.accumulate(dz[::-1])[::-1]                # ниже — не меньше (коса не загибается внутрь)
        dz = np.convolve(np.pad(dz, 2, mode='edge'), np.ones(5) / 5, mode='valid')
        dz *= smoothstep(1.30, 1.22, YG)                          # у затылка коса остаётся на месте
        sel = m_
        HP[sel, 2] += np.interp(HP[sel, 1], YG, dz)
        braid_surf[side] = []
        for y in np.arange(0.80, 1.29, 0.04):                     # точки поверхности ткани за косой
            sl = m_ & (np.abs(HP[:, 1] - y) < 0.015)
            if sl.sum() < 4:
                continue
            xc = float(np.median(HP[sl, 0]))
            cs = (np.abs(CLV[:, 1] - y) < 0.015) & (np.abs(CLV[:, 0] - xc) < 0.03) & (CLV[:, 2] > 0)
            if cs.any():
                braid_surf[side].append((xc, float(y), float(CLV[cs, 2].max())))
        print(f'braid {side}: сдвиг наружу до {dz.max() * 100:.1f} см')
    write_back(J, BIN, hattrs['POSITION'], HP)
    acc = J['accessors'][hattrs['POSITION']]
    acc['min'] = HP.min(axis=0).tolist(); acc['max'] = HP.max(axis=0).tolist()
    braid_chains = []
    for side, sgn in (('L', -1), ('R', 1)):
        pts = []
        for y in HB:
            sel = (np.abs(HP[:, 1] - y) < 0.035) & (HP[:, 0] * sgn > 0.01) & (HP[:, 2] > 0.05)
            if sel.sum() < 5:
                sel = (np.abs(HP[:, 1] - y) < 0.06) & (HP[:, 0] * sgn > 0.0) & (HP[:, 2] > 0.05)
            c = HP[sel].mean(axis=0)
            pts.append([c[0], y, c[2]])
        braid_chains.append(add_chain(f'J_Sec_{side}_HairBraid', head, pts))

    # ---------------- ПОЛЯ ШЛЯПЫ ----------------
    # Шляпа — часть волос (HAIR_02 выше 1.49 + лента HAIR_03), вся на голове. Поля — 8 лучей по 2 кости: руки,
    # поднятые над головой, приподнимают край полей, а не проходят сквозь него. Тулья и кончик остаются на голове —
    # их мягкость делает шейдер страницы.
    hp2 = np.unique(view(J, BIN, hm['primitives'][1]['indices']).ravel())
    hat_v = hp2[HP[hp2, 1] > 1.49]
    brim_v = hat_v[HP[hat_v, 1] < 1.545]
    bc = HP[brim_v][:, [0, 2]].mean(axis=0)
    brr = np.hypot(HP[brim_v, 0] - bc[0], HP[brim_v, 2] - bc[1])
    bth = np.degrees(np.arctan2(HP[brim_v, 0] - bc[0], HP[brim_v, 2] - bc[1]))
    BR = [0.13, 0.19, 0.255]
    BANG = np.arange(0, 360, 45) - 180.0
    brim_chains = []
    for t in BANG:
        pts = []
        for r in BR:
            d = np.abs(((bth - t) + 180) % 360 - 180)
            near = (np.abs(brr - r) < 0.03) & (d < 25)
            yy = float(np.median(HP[brim_v[near], 1])) if near.sum() >= 3 else 1.52
            pts.append([bc[0] + r * np.sin(np.radians(t)), yy, bc[1] + r * np.cos(np.radians(t))])
        brim_chains.append(add_chain(f'J_Sec_C_HatBrim{int(t + 180):03d}_', head, pts))

    # ---------------- ТУЛЬЯ И КОНЧИК ШЛЯПЫ ----------------
    # Своих костей у тульи не было (мягкость изображал шейдер страницы — почти незаметно). Цепочка из 4 костей
    # по средней линии: от основания тульи вверх и по смятому кончику назад-влево. Пружина: кончик покачивается
    # при ходьбе, прыжках и поворотах головы, руки над головой его отталкивают.
    HV = HP[hat_v]
    def cen(m_):
        return HV[m_].mean(axis=0)
    crown_pts = [cen(np.abs(HV[:, 1] - 1.59) < 0.012),
                 cen((HV[:, 1] > 1.655) & (HV[:, 1] < 1.70) & (HV[:, 2] < 0.10)),
                 cen(HV[:, 1] > HV[:, 1].max() - 0.02),
                 cen((HV[:, 1] > 1.62) & (HV[:, 2] > 0.13) & (HV[:, 2] < 0.17)),
                 cen((HV[:, 1] > 1.6) & (HV[:, 2] > HV[HV[:, 1] > 1.6, 2].max() - 0.025))]
    crown_pts[0][1] = 1.59
    crown_chain = add_chain('J_Sec_C_HatCrown', head, [p_.tolist() for p_ in crown_pts])
    print('hat crown chain:', np.round(np.array(crown_pts), 3).tolist())

    # ---------------- ЮБКА ----------------
    # Было 6 цепочек по ~2 кости (перед/бок/зад), перед и зад висели на бёдрах: юбка ломалась углами.
    # Стало 12 цепочек по кругу (через 30°) по 3 кости, все от таза; ноги отталкивают их коллайдерами.
    skirt_prims = [p for p in bm['primitives'] if J['materials'][p['material']]['name'].startswith('N00_002_01_Tops')]
    assert len(skirt_prims) == 1
    sv = np.unique(view(J, BIN, skirt_prims[0]['indices']).ravel())
    S = P[sv]
    sth = np.degrees(np.arctan2(S[:, 0], S[:, 2]))
    HS = [0.93, 0.82, 0.71, 0.60]
    SANG = np.arange(15, 360, 30) - 180.0                # -165 … 165: ни одной цепочки ровно посередине
    hips = name2i['J_Bip_C_Hips']
    # «корень колен»: узел в тазобедренном суставе, на тазе. vrm.html каждый кадр поворачивает его на половину
    # поворота бедра — перед юбки сидя ложится на бёдра, но у паха не задирается вверх, как при посадке прямо на бедро.
    lap = {s_: add_node(f'J_Sec_{s_}_SkirtLapRoot', hips, wpos(name2i[f'J_Bip_{s_}_UpperLeg'])) for s_ in 'LR'}
    skirt_chains = []
    for t in SANG:
        pts = []
        for y in HS:
            d = np.abs(((sth - t) + 180) % 360 - 180)
            near = (np.abs(S[:, 1] - y) < 0.05) & (d < 18)
            if near.sum() < 3:
                near = np.argsort(np.abs(S[:, 1] - y) * 4 + d / 60)[:10]
            r = float(np.median(np.hypot(S[near, 0], S[near, 2]))) - 0.006
            pts.append([r * np.sin(np.radians(t)), y, r * np.cos(np.radians(t))])
        side = 'L' if t < 0 else 'R'
        part = 'Back' if abs(t) < 50 else ('Side' if abs(t) < 130 else 'Front')
        # зад — на бедре своей стороны (как в исходной модели), перед — на «корне колен» (полуповорот бедра):
        # на тазе юбка сидя упиралась в бёдра и топорщилась вверх. Бока — на тазе.
        parent = hips if part == 'Side' else lap[side] if part == 'Front' else name2i[f'J_Bip_{side}_UpperLeg']
        skirt_chains.append(add_chain(f'J_Sec_{side}_SkirtFine{part}{int(abs(t)):03d}_', parent, pts))

    # ---------------- СКИН: новые суставы + матрицы привязки ----------------
    def extend_skin(si, new_nodes):
        sk = J['skins'][si]
        old = view(J, BIN, sk['inverseBindMatrices']).copy()
        mats = [old]
        for ni in new_nodes:
            m = np.eye(4); m[:3, 3] = -wpos(ni)                 # поворотов в модели нет — только сдвиг
            mats.append(m.T.reshape(1, 16))                       # glTF хранит по столбцам
        data = np.concatenate(mats).astype(np.float32).tobytes()
        while len(BIN) % 4:
            BIN.extend(b'\x00')
        off = len(BIN); BIN.extend(data)
        J['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': len(data)})
        J['accessors'].append({'bufferView': len(J['bufferViews']) - 1, 'componentType': 5126,
                               'count': len(sk['joints']) + len(new_nodes), 'type': 'MAT4'})
        base = len(sk['joints'])
        sk['joints'] = sk['joints'] + list(new_nodes)
        sk['inverseBindMatrices'] = len(J['accessors']) - 1
        return {ni: base + k for k, ni in enumerate(new_nodes)}

    coat_bones = [ni for ch in coat_chains for ni in ch[:-1]]      # кончики (_end) веса не получают
    braid_bones = [ni for ch in braid_chains for ni in ch[:-1]]
    coat_slot = extend_skin(body_skin, [ni for ch in coat_chains for ni in ch] + [ni for ch in skirt_chains for ni in ch])
    braid_slot = extend_skin(hair_skin, [ni for ch in braid_chains for ni in ch] + [ni for ch in brim_chains for ni in ch] + crown_chain)

    def chain_weights(y, H):
        """Веса по костям цепочки (палатки с центрами в серединах костей) для высоты y."""
        n = len(H) - 1
        if y >= H[0]:
            s = 0.0
        elif y <= H[-1]:
            s = n - 1e-6
        else:
            k = max(i for i in range(n) if H[i] >= y)
            s = k + (H[k] - y) / (H[k] - H[k + 1])
        w = np.array([max(0.0, 1 - abs(s - (k + 0.5))) for k in range(n)])
        if s <= 0.5: w[:] = 0; w[0] = 1
        if s >= n - 0.5: w[:] = 0; w[-1] = 1
        return w / w.sum()

    def blend(vi, JOa, WEa, new):
        """new — {слот сустава: вес} (сумма 1) с силой k уже учтённой снаружи: смешиваем и оставляем 4 главных."""
        d = {}
        for c in range(4):
            if WEa[vi, c] > 0:
                d[int(JOa[vi, c])] = d.get(int(JOa[vi, c]), 0) + float(WEa[vi, c])
        k = new.pop('_k')
        d = {s: w * (1 - k) for s, w in d.items()}
        for s, w in new.items():
            d[s] = d.get(s, 0) + w * k
        top = sorted(d.items(), key=lambda x: -x[1])[:4]
        tot = sum(w for _, w in top)
        for c in range(4):
            if c < len(top):
                JOa[vi, c] = top[c][0]; WEa[vi, c] = top[c][1] / tot
            else:
                JOa[vi, c] = 0; WEa[vi, c] = 0

    # плащ: k по высоте — выше корня всё как было, ниже 1.10 целиком на цепочках
    for idx, vi in enumerate(cv):
        x, y, z = P[vi]
        k = float(smoothstep(HC[0] + 0.12, HC[0] - 0.05, y))     # плавная сшивка с верхом плаща (на груди)
        if k <= 0:
            continue
        u = np.clip(th[idx] / max(thmax_at(y), 1.0), -1, 1)
        if u <= U[0]:
            pairs = [(0, 1.0)]
        elif u >= U[-1]:
            pairs = [(len(U) - 1, 1.0)]
        else:
            i = int(np.searchsorted(U, u) - 1)
            a = (u - U[i]) / (U[i + 1] - U[i])
            pairs = [(i, 1 - a), (i + 1, a)]
        cw = chain_weights(y, HC)
        new = {'_k': k}
        for ci, aw in pairs:
            for b, bw in enumerate(cw):
                if aw * bw > 1e-4:
                    s = coat_slot[coat_chains[ci][b]]
                    new[s] = new.get(s, 0) + aw * bw
        blend(vi, JO, WE, new)

    # юбка: выше пояса — как было, ниже — по кругу между соседними цепочками и по высоте между костями
    for idx, vi in enumerate(sv):
        y = P[vi, 1]
        k = float(smoothstep(HS[0] + 0.03, HS[0] - 0.05, y))
        if k <= 0:
            continue
        t = sth[idx]
        i = int(np.floor((t - SANG[0]) / 30.0)) % 12
        a = (((t - SANG[i]) + 360) % 360) / 30.0
        pairs = [(i, 1 - a), ((i + 1) % 12, a)]
        cw = chain_weights(y, HS)
        new = {'_k': k}
        for ci, aw in pairs:
            for b, bw in enumerate(cw):
                if aw * bw > 1e-4:
                    sl = coat_slot[skirt_chains[ci][b]]
                    new[sl] = new.get(sl, 0) + aw * bw
        blend(vi, JO, WE, new)

    # косички: всё, что ниже корня косы (это только косы с бантами), плавно переходит на цепочку СВОЕЙ косы.
    # Сторону выбираем не по x (банты на концах заходят за середину — их рвало пополам), а по кускам сетки:
    # каждый связный кусок (коса, бант) ниже y=1.25 целиком идёт к ближайшей косе; выше — по ближайшей оси косы.
    cand = np.where((HP[:, 1] < HB[0] + 0.02) & (HP[:, 2] >= 0.04))[0]
    cset = set(cand.tolist())
    parent_uf = {v: v for v in cand.tolist()}

    def find(v):
        while parent_uf[v] != v:
            parent_uf[v] = parent_uf[parent_uf[v]]; v = parent_uf[v]
        return v
    for p in hm['primitives']:
        tri = view(J, BIN, p['indices']).reshape(-1, 3)
        for t in tri:
            low = [int(v) for v in t if int(v) in cset and HP[int(v), 1] < 1.25]
            for v in low[1:]:
                ra, rb = find(low[0]), find(v)
                if ra != rb: parent_uf[ra] = rb
    axes = [np.array([wpos(ni) for ni in ch]) for ch in braid_chains]

    def axis_dist(pt, ax):
        return float(np.min(np.linalg.norm(ax[:, [0, 2]] - pt[[0, 2]], axis=1) + np.abs(ax[:, 1] - pt[1]) * 0.3))
    comp_side = {}
    for v in cand.tolist():
        if HP[v, 1] < 1.25:
            comp_side.setdefault(find(v), []).append(v)
    comp_side = {r: (0 if np.mean([axis_dist(HP[u], axes[0]) - axis_dist(HP[u], axes[1]) for u in vs]) < 0 else 1)
                 for r, vs in comp_side.items()}
    print('braid pieces below 1.25:', len(comp_side), ' -> L', sum(1 for v in comp_side.values() if v == 0), 'R', sum(1 for v in comp_side.values() if v == 1))
    for vi in cand:
        x, y, z = HP[vi]
        k = float(smoothstep(HB[0] + 0.02, HB[0] - 0.07, y))
        if k <= 0:
            continue
        if y < 1.25:
            side = comp_side[find(int(vi))]
        else:
            side = 0 if axis_dist(HP[vi], axes[0]) < axis_dist(HP[vi], axes[1]) else 1
        ch = braid_chains[side]
        cw = chain_weights(y, HB)
        new = {'_k': k}
        for b, bw in enumerate(cw):
            if bw > 1e-4:
                new[braid_slot[ch[b]]] = bw
        blend(vi, HJO, HWE, new)

    # поля: от 13 см от центра плавно переходят на лучи; по кругу — между соседними лучами, по радиусу — между костями
    for vi, r, t in zip(brim_v, brr, bth):
        k = float(smoothstep(0.115, 0.15, r))
        if k <= 0:
            continue
        i = int(np.floor((t - BANG[0]) / 45.0)) % 8
        a = (((t - BANG[i]) + 360) % 360) / 45.0
        cw = chain_weights(-r, [-x for x in BR])
        new = {'_k': k}
        for ci, aw in ((i, 1 - a), ((i + 1) % 8, a)):
            for b, bw in enumerate(cw):
                if aw * bw > 1e-4:
                    sl = braid_slot[brim_chains[ci][b]]
                    new[sl] = new.get(sl, 0) + aw * bw
        blend(vi, HJO, HWE, new)

    # тулья: параметр u вдоль средней линии (0 — основание, 4 — кончик); веса — «палатки» по костям,
    # доля цепочки растёт от основания (u 0.15…0.6) — у ленты шляпа остаётся на голове
    CP = np.array(crown_pts)
    nb = len(CP) - 1
    ncr = 0
    for vi in hat_v:
        if HP[vi, 1] < 1.585:
            continue
        best = (1e9, 0.0)
        for b in range(nb):
            a0, b0 = CP[b], CP[b + 1]; ab = b0 - a0
            t_ = float(np.clip((HP[vi] - a0) @ ab / (ab @ ab), 0, 1))
            d_ = float(np.linalg.norm(HP[vi] - (a0 + ab * t_)))
            if d_ < best[0]:
                best = (d_, b + t_)
        u = best[1]
        k = float(smoothstep(0.15, 0.6, u))
        if k <= 0:
            continue
        uc = min(max(u, 0.5), nb - 0.5)
        new = {'_k': k}
        tot = 0.0
        for b in range(nb):
            bw = max(0.0, 1 - abs(uc - (b + 0.5)))
            if bw > 1e-4:
                new[braid_slot[crown_chain[b]]] = bw; tot += bw
        for kk in list(new):
            if kk != '_k':
                new[kk] /= tot
        blend(vi, HJO, HWE, new)
        ncr += 1
    print('hat crown verts:', ncr)

    # ---------------- ПРУЖИНЫ VRM 0.x ----------------
    sa = J['extensions']['VRM']['secondaryAnimation']
    cgs = sa['colliderGroups']

    def cg_index(node_name):
        return [k for k, g in enumerate(cgs) if nodes[g['node']]['name'] == node_name]

    def add_cg(node_name, spheres):
        cgs.append({'node': name2i[node_name], 'colliders': [{'offset': {'x': float(o[0]), 'y': float(o[1]), 'z': float(o[2])}, 'radius': float(r)} for o, r in spheres]})
        return len(cgs) - 1

    def along(a, b, r, step=0.045):
        """Шарики вдоль кости a→b в локальных координатах a."""
        pa, pb = wpos(name2i[a]), wpos(name2i[b]); L = np.linalg.norm(pb - pa); d = (pb - pa) / L
        n = max(2, int(L / step) + 1)
        return [((d * L * t).tolist(), r) for t in np.linspace(0, 1.0, n)]

    arm_cg, upper_arm_cg = [], []
    for s in 'LR':
        arm_cg.append(add_cg(f'J_Bip_{s}_UpperArm', along(f'J_Bip_{s}_UpperArm', f'J_Bip_{s}_LowerArm', 0.06)))
        upper_arm_cg.append(arm_cg[-1])
        arm_cg.append(add_cg(f'J_Bip_{s}_LowerArm', along(f'J_Bip_{s}_LowerArm', f'J_Bip_{s}_Hand', 0.058)))
        arm_cg.append(add_cg(f'J_Bip_{s}_Hand', [([0.035 * (-1 if s == 'L' else 1), 0, 0], 0.045)]))
    # ноги для юбки: бедро толще, плюс колено — в воздухе/в руке ноги сгибаются, колено уходило в юбку
    legs_skirt_cg = []
    for s_ in 'LR':
        legs_skirt_cg.append(add_cg(f'J_Bip_{s_}_UpperLeg', along(f'J_Bip_{s_}_UpperLeg', f'J_Bip_{s_}_LowerLeg', LEG_R, 0.06)))
        legs_skirt_cg.append(add_cg(f'J_Bip_{s_}_LowerLeg', [([0, 0, 0], KNEE_R), ([0, -0.07, 0], KNEE_R * 0.9)]))
    hips_cg = add_cg('J_Bip_C_Hips', [([0, 0.06, 0.0], 0.115), ([-0.07, -0.03, 0.0], 0.1), ([0.07, -0.03, 0.0], 0.1)])
    body_cg = cg_index('J_Bip_C_Spine') + cg_index('J_Bip_C_UpperChest')

    # старые 6 цепочек юбки — из физики (веса с них сняты), их коллайдеры ног — новым
    old_skirt = [g for g in sa['boneGroups'] if g.get('comment') == 'Skirt']
    leg_cg = old_skirt[0]['colliderGroups'] if old_skirt else cg_index('J_Bip_L_UpperLeg') + cg_index('J_Bip_R_UpperLeg')
    sa['boneGroups'] = [g for g in sa['boneGroups'] if g.get('comment') != 'Skirt']
    sa['boneGroups'].append({
        'comment': 'Skirt', 'stiffiness': 0.55, 'gravityPower': 0.05, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
        'dragForce': 0.12, 'center': -1, 'hitRadius': 0.016,
        'bones': [ch[0] for ch in skirt_chains], 'colliderGroups': legs_skirt_cg + [hips_cg]})
    # Ткань с тканью в пружинах не сталкивается — поэтому препятствия вешаем прямо на кости:
    #  * шарики на костях юбки: плащ огибает юбку (и поднятую ветром тоже), а не проходит сквозь неё;
    #  * шарики на костях спинки плаща: косички лежат поверх плаща, а не проваливаются в него.
    skirt_bulk_cg = []
    for ch in skirt_chains:
        for ni in ch[1:]:                                       # суставы ниже пояса и подол
            # шарик утоплен внутрь юбки: наружу выступает только ~1 см — плащ ложится на юбку, а не отскакивает
            p = wpos(ni); dirin = -np.array([p[0], 0, p[2]]) / max(np.hypot(p[0], p[2]), 1e-6)
            cgs.append({'node': ni, 'colliders': [{'offset': {'x': float(dirin[0] * 0.035), 'y': 0, 'z': float(dirin[2] * 0.035)}, 'radius': 0.045}]})
            skirt_bulk_cg.append(len(cgs) - 1)
    # Препятствия для кос — шарики под самой тканью за косой (поверхность шарика = поверхность ткани):
    # у накидки — на верхней груди, ниже — на ближайших костях спинки плаща (едут вместе с ним).
    # Центр шарика — внутри, под тканью: коса, лежащая сверху, выталкивается наружу, а не внутрь.
    braid_cloth_cg = []
    BR_R = 0.12                          # крупные: центр глубоко под тканью — коса с любой стороны выталкивается наружу
    back_joints = [ni for ch in coat_chains if 'SideA' not in nodes[ch[0]]['name'] for ni in ch[:-1]]
    upper = name2i['J_Bip_C_UpperChest']
    hips_node = name2i['J_Bip_C_Hips']
    for side in 'LR':
        for xc, y, zs in braid_surf[side]:
            c = np.array([xc, y, zs - BR_R])
            if y > 1.12:
                ni = upper
            else:
                # кость, которая ведёт отрезок плаща на этой высоте (шарик поворачивается вместе с ним);
                # выше корня цепочки — таз
                ch = next(c_ for c_ in coat_chains if 'LongBack' in nodes[c_[0]]['name'] and nodes[c_[0]]['name'][6] == side)
                ni = hips_node
                for k in range(len(ch) - 1):
                    if wpos(ch[k])[1] >= y > wpos(ch[k + 1])[1]:
                        ni = ch[k]
            o = c - wpos(ni)
            # VRM 0.x хранит смещение коллайдера с обратным знаком z (так его читает three-vrm) — проверено в странице
            cgs.append({'node': ni, 'colliders': [{'offset': {'x': float(o[0]), 'y': float(o[1]), 'z': float(-o[2])}, 'radius': BR_R}]})
            braid_cloth_cg.append(len(cgs) - 1)
    print('braid cloth colliders:', len(braid_cloth_cg))
    coat_back_cg = []
    for ch in coat_chains:
        if 'SideA' in nodes[ch[0]]['name']:
            continue
        for k, ni in enumerate(ch[:-1]):
            # шарики чуть внутрь (к телу): у сустава и на полпути к следующему — без щелей, куда проваливалась коса
            p = wpos(ni); dirin = -np.array([p[0], 0, p[2]]) / max(np.hypot(p[0], p[2]), 1e-6)
            half = np.array(nodes[ch[k + 1]]['translation']) * 0.5
            cols = []
            for o in (np.zeros(3), half):
                c = o + dirin * BACK_IN
                cols.append({'offset': {'x': float(c[0]), 'y': float(c[1]), 'z': float(c[2])}, 'radius': BACK_R})
            cgs.append({'node': ni, 'colliders': cols})
            coat_back_cg.append(len(cgs) - 1)
    # Плащ в модели расходится назад колоколом: сильная гравитация тянула низ к телу — в ноги и юбку.
    # Поэтому жёстче и почти без гравитации (держит форму), а ноги и юбка его отталкивают.
    sa['boneGroups'].append({
        'comment': 'CoatLong', 'stiffiness': 1.4, 'gravityPower': 0.04, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
        'dragForce': 0.5, 'center': -1, 'hitRadius': 0.035,
        'bones': [ch[0] for ch in coat_chains], 'colliderGroups': upper_arm_cg + body_cg + [hips_cg] + legs_skirt_cg + skirt_bulk_cg})
    # (предплечье и кисть — не препятствие для костей плаща: они отталкивали кость в любую сторону, и плащ
    #  оказывался ПЕРЕД рукой; с предплечьем разбирается шейдер страницы — он всегда ставит руку перед тканью)
    sa['boneGroups'].append({
        'comment': 'HairBraid', 'stiffiness': 0.7, 'gravityPower': 0.35, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
        'dragForce': 0.35, 'center': -1, 'hitRadius': 0.02,
        'bones': [ch[0] for ch in braid_chains], 'colliderGroups': body_cg + [hips_cg] + arm_cg + braid_cloth_cg})

    # поля шляпы: жёсткие (держат форму), почти без веса; руки их приподнимают
    sa['boneGroups'].append({
        'comment': 'HatBrim', 'stiffiness': 3.0, 'gravityPower': 0.02, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
        'dragForce': 0.6, 'center': -1, 'hitRadius': 0.02,
        'bones': [ch[0] for ch in brim_chains], 'colliderGroups': arm_cg})

    # тулья: держит форму у основания, кончик мягкий и слегка тяжёлый — качается и догоняет голову
    sa['boneGroups'].append({
        'comment': 'HatCrown', 'stiffiness': 1.8, 'gravityPower': 0.04, 'gravityDir': {'x': 0, 'y': -1, 'z': 0},
        'dragForce': 0.28, 'center': -1, 'hitRadius': 0.035,
        'bones': [crown_chain[0]], 'colliderGroups': arm_cg})

    # ---------------- НАКИДКА У ЛОКТЯ ----------------
    # Рукав накидки свисает за локоть, и в исходной модели соседние вершины его каймы привязаны к предплечью
    # совсем по-разному (0.04 и 1.0 рядом). Согнула локоть — кайма мнётся, белая полоса то толще, то тоньше.
    # Делаем долю предплечья плавной функцией только положения вдоль руки: вся петля каймы на одном уровне
    # движется одинаково, и полоса держит ширину. Остальные кости (грудь, плечо) не трогаем.
    sk_body = J['skins'][body_skin]['joints']
    slot_of = {ni: k for k, ni in enumerate(sk_body)}
    cape_prim = next(p for p in bm['primitives'] if J['materials'][p['material']]['name'].startswith('N00_007_03_Tops_01_CLOTH_03'))
    capv = np.unique(view(J, BIN, cape_prim['indices']).ravel())
    nfix = 0
    for sd in 'LR':
        ua, la = name2i[f'J_Bip_{sd}_UpperArm'], name2i[f'J_Bip_{sd}_LowerArm']
        su, sl = slot_of[ua], slot_of[la]
        A, Bp = wpos(ua), wpos(la)
        ax = Bp - A; L = np.linalg.norm(ax); ax /= L
        for v in capv:
            row = {int(JO[v, k]): float(WE[v, k]) for k in range(4) if WE[v, k] > 0}
            arm = row.get(su, 0.0) + row.get(sl, 0.0)
            if arm < 0.05:
                continue
            t = float((P[v] - A) @ ax / L)
            if t < 0.45:
                continue
            f = 0.6 * smoothstep(0.7, 1.15, t)          # к краю рукава — больше за предплечьем, но не целиком
            row[su] = arm * (1 - f); row[sl] = arm * f
            items = sorted(((w, j) for j, w in row.items() if w > 1e-4), reverse=True)[:4]
            tot = sum(w for w, _ in items)
            for k in range(4):
                if k < len(items):
                    JO[v, k] = items[k][1]; WE[v, k] = items[k][0] / tot
                else:
                    JO[v, k] = 0; WE[v, k] = 0.0
            nfix += 1
    print('cape sleeve verts smoothed:', nfix)
    # Сглаживание весов накидки по сетке: на границе «грудь ↔ плечо» вес менялся скачком, и когда руки подняты
    # (держат на руках, потягивается), перед накидки у подмышки ломался резким углом и кайма обрывалась.
    # Несколько проходов «вес = среднее с соседями» — переход плавный, форма в покое та же (позу покоя веса не меняют).
    tri = view(J, BIN, cape_prim['indices']).reshape(-1, 3)
    nb = {}
    for a_, b_, c_ in tri:
        for x, y in ((a_, b_), (b_, c_), (c_, a_)):
            nb.setdefault(int(x), set()).add(int(y)); nb.setdefault(int(y), set()).add(int(x))
    # совпадающие вершины на швах UV — тоже соседи, иначе шов остаётся ступенькой
    key = {}
    for v in capv:
        key.setdefault(tuple(np.round(P[v], 4)), []).append(int(v))
    for grp in key.values():
        for v in grp:
            nb.setdefault(v, set()).update(u for u in grp if u != v)
    order = [int(v) for v in capv]
    Wd = {v: {int(JO[v, k]): float(WE[v, k]) for k in range(4) if WE[v, k] > 0} for v in order}
    for it in range(CAPE_SMOOTH):
        new = {}
        for v in order:
            acc = dict((j, 0.5 * w) for j, w in Wd[v].items())
            ns = nb.get(v, ())
            if ns:
                f = 0.5 / len(ns)
                for u in ns:
                    for j, w in Wd[u].items():
                        acc[j] = acc.get(j, 0.0) + f * w
            new[v] = acc
        Wd = new
    W0 = {v: {int(JO[v, k]): float(WE[v, k]) for k in range(4) if WE[v, k] > 0} for v in order}
    arm_seg = {s_: (wpos(name2i[f'J_Bip_{s_}_UpperArm']), wpos(name2i[f'J_Bip_{s_}_Hand'])) for s_ in 'LR'}
    def seg_d(p_, a0, b0):
        ab = b0 - a0; t_ = np.clip((p_ - a0) @ ab / (ab @ ab), 0, 1)
        return float(np.linalg.norm(p_ - (a0 + ab * t_)))
    for v in order:
        # только у подола (низ накидки): выше, над руками, сглаживание оттягивало накидку от руки, и рукав жакета
        # протыкал её белыми полосками
        m = float(smoothstep(CAPE_SM_TOP, CAPE_SM_TOP - 0.06, P[v, 1]))
        # и не над самой рукой (там накидка должна идти за рукой целиком, иначе рукав жакета протыкает её)
        m *= float(smoothstep(0.11, 0.17, min(seg_d(P[v], *arm_seg[s_]) for s_ in 'LR')))
        mix = dict((j, (1 - m) * w) for j, w in W0[v].items())
        for j, w in Wd[v].items():
            mix[j] = mix.get(j, 0.0) + m * w
        Wd[v] = mix
    for v in order:
        items = sorted(((w, j) for j, w in Wd[v].items() if w > 1e-4), reverse=True)[:4]
        tot = sum(w for w, _ in items)
        for k in range(4):
            JO[v, k], WE[v, k] = (items[k][1], items[k][0] / tot) if k < len(items) else (0, 0.0)
    print('cape weights smoothed:', len(order), 'verts x', CAPE_SMOOTH)
    write_back(J, BIN, attrs['JOINTS_0'], JO); write_back(J, BIN, attrs['WEIGHTS_0'], WE)
    write_back(J, BIN, hattrs['JOINTS_0'], HJO); write_back(J, BIN, hattrs['WEIGHTS_0'], HWE)
    save(dst, J, BIN)
    print('coat chains:', [[nodes[i]['name'] for i in ch[:1]] for ch in coat_chains])
    print('braid chains:', [nodes[ch[0]]['name'] for ch in braid_chains])
    print('skirt chains:', len(skirt_chains), [nodes[ch[0]]['name'] for ch in skirt_chains][:3], '…')
    print('coat verts re-weighted:', len(cv), ' braid verts:', int(((HP[:, 1] < HB[0] + 0.02) & (HP[:, 2] >= 0.04)).sum()))
    return coat_chains, braid_chains


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
