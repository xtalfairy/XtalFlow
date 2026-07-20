import asyncio
from typing import Dict, Any

class Lims:
    async def get_soak_candidates(self) -> list[Dict[str, Any]]: ...
    async def set_status(self, sample_uid: str, status: str, **kw): ...
    async def write_audit(self, sample_uid: str, action: str, payload: dict): ...

class ImagingAdapter:
    async def ingest_results(self) -> None: ...  # watch-folder/SDK → LIMS 업데이트

class LiquidAdapter:
    async def run_soak(self, task: dict) -> None: ...  # JSON → 벤더포맷 변환 → 실행

class ShifterAdapter:
    async def harvest(self, sample_uid: str) -> None: ...  # 좌표 변환 → EPICS/SDK 호출

class Orchestrator:
    def __init__(self, lims, img, liq, shf):
        self.lims, self.img, self.liq, self.shf = lims, img, liq, shf

    async def imaging_loop(self):
        while True:
            try:
                await self.img.ingest_results()
            except Exception as e:
                pass
            await asyncio.sleep(5)

    async def soaking_loop(self):
        while True:
            tasks = await self.lims.get_soak_candidates()
            for t in tasks:
                uid = t["sample_uid"]
                try:
                    await self.lims.set_status(uid, "SOAKING")
                    await self.liq.run_soak(t)
                    await self.lims.set_status(uid, "SOAK_DONE")
                except Exception as e:
                    await self.lims.write_audit(uid, "soak_error", {"err": str(e), "task": t})
                    await self.lims.set_status(uid, "ERROR", stage="SOAK")
            await asyncio.sleep(2)

    async def harvest_loop(self):
        while True:
            # HARVEST_PENDING 큐를 LIMS에서 조회
            # 예: get_harvest_queue()
            pending = []  # ← 구현
            for uid in pending:
                try:
                    await self.lims.set_status(uid, "HARVESTING")
                    await self.shf.harvest(uid)
                    await self.lims.set_status(uid, "HARVESTED")
                except Exception as e:
                    await self.lims.write_audit(uid, "harvest_error", {"err": str(e)})
                    await self.lims.set_status(uid, "ERROR", stage="HARVEST")
            await asyncio.sleep(2)

async def main():
    lims = Lims(); img = ImagingAdapter(); liq = LiquidAdapter(); shf = ShifterAdapter()
    orch = Orchestrator(lims, img, liq, shf)
    await asyncio.gather(
        orch.imaging_loop(),
        orch.soaking_loop(),
        orch.harvest_loop(),
    )

if __name__ == "__main__":
    asyncio.run(main())


