"""Random mainland-city source sampling; no city is silently removed from results."""
import json,random,time
import numpy as np
from global_source import PublicSource,xyz

cities=[('哈尔滨',45.75,126.65),('北京',39.90,116.40),('济南',36.67,117.00),('南京',32.06,118.79),('上海',31.23,121.47),('武汉',30.59,114.30),('成都',30.67,104.07),('重庆',29.56,106.55),('西安',34.34,108.94),('广州',23.13,113.26),('昆明',25.04,102.71),('南宁',22.82,108.37),('兰州',36.06,103.83),('乌鲁木齐',43.82,87.62),('沈阳',41.80,123.43),('长沙',28.23,112.94),('福州',26.08,119.30),('郑州',34.75,113.62)]
s=PublicSource();m=s.metadata();frame=m['radar']['past'][-1]
chosen=random.SystemRandom().sample(cities,8)
result={'sampled_at':int(time.time()),'frame_generation_time':frame['time'],'frame_age_seconds':round(time.time()-frame['time']),'method':'uniform random without replacement from 18 declared mainland cities; one XYZ z7 per city, all sampled results retained','results':[]}
for name,lat,lon in chosen:
    tile=xyz(lat,lon,7)
    row={'city':name,'tile':tile}
    try:
        image=np.asarray(s.tile(m,frame,*tile));cover=np.asarray(s.coverage(*tile))[:,:,3]==0
        row.update(static_coverage_pixels=int(cover.sum()),visible_echo_pixels=int((image[:,:,3]>0).sum()),visible_echo_in_static_coverage=int(((image[:,:,3]>0)&cover).sum()),pixels=int(cover.size))
    except Exception as exc: row['error']=type(exc).__name__
    result['results'].append(row)
    print(json.dumps(row,ensure_ascii=False),flush=True)
with open('sample-mainland-20261107.json','w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print('saved sample-mainland-20261107.json',flush=True)
