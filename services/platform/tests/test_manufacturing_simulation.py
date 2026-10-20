"""合成器仅写本测试新建的隔离导入源，分步生成可停止。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.settings import Settings
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.models import Dataset
from agent_platform.modules.manufacturing.simulation import SimulationService
from agent_platform.modules.manufacturing.schemas import SimulationAdvance, SimulationInput


def test_generator_is_explicit_bounded_and_marks_synthetic(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "simulation.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("simulation-test", "workspace", frozenset({"*"}), ("admin",))
    settings = Settings(data_dir=tmp_path / "simulation-data")
    with Session(engine) as session:
        service = SimulationService(session, actor, settings)
        assert service.latest()["status"] == "stopped"
        activation = service.create(SimulationInput(start_at="2026-10-20T08:00:00+08:00", steps=2))
        assert activation["status"] == "queued"
        assert activation["sample_count"] == 0
        advanced = service.advance(activation["id"], SimulationAdvance(revision=activation["revision"], steps=1))
        assert advanced["status"] == "running"
        completed = service.advance(activation["id"], SimulationAdvance(revision=advanced["revision"], steps=1))
        assert completed["status"] == "succeeded"
        assert completed["sample_count"] == 16
        data = DataService(session, actor, settings)
        dataset = data.get(Dataset, completed["dataset_id"])
        assert dataset.source_kind == "synthetic"
        query = data.execute_query({"datasource_id": dataset.datasource_id, "sql": f'SELECT quality,simulation_version FROM "{dataset.table_name}" LIMIT 2'})
        assert query["rows"][0]["quality"].startswith("SIMULATED_")
        cancelled = service.create(SimulationInput(start_at="2026-10-20T08:00:00+08:00", steps=2))
        stopped = service.cancel(cancelled["id"], cancelled["revision"])
        assert stopped["status"] == "cancelled"
        assert stopped["dataset_id"] is None
        session.rollback()
    engine.dispose()
