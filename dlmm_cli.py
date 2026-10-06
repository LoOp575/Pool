import argparse,json
from dlmm_lp_engine import Candle,PoolConfig,Distribution,analyze,format_report
p=argparse.ArgumentParser()
p.add_argument("json"); p.add_argument("--active-bin",type=int,required=True); p.add_argument("--bin-step-bps",type=float,required=True)
p.add_argument("--fee-apr",type=float,default=0); p.add_argument("--horizon",type=int,default=24); p.add_argument("--paths",type=int,default=2000)
p.add_argument("--distribution",choices=[x.value for x in Distribution])
a=p.parse_args()
rows=json.load(open(a.json,encoding="utf-8")); candles=[Candle(**x) for x in rows]
x=analyze(candles,PoolConfig(a.active_bin,a.bin_step_bps),a.fee_apr,a.horizon,a.paths,Distribution(a.distribution) if a.distribution else None)
print(format_report(x)); print(json.dumps(x.to_dict(),indent=2))
