"""PC 에서 전체 기능 화면(웹앱) 띄우기.

배포 꾸러미의 웹앱 폴더를 찾아 로컬 서버로 내어 준다. 여기서 지키는 것은 셋이다.

* 폴더를 **어디에서 찾는지** — 실행 파일 옆, 개발 트리, 환경변수
* 못 찾았을 때 **어디를 뒤졌는지 말하는지** — 폴더를 옮겨 둔 사람이 스스로 고쳐야 한다
* ``.wasm`` 의 MIME — 틀리면 브라우저가 엔진을 아예 띄우지 못한다
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from pension import localapp


@pytest.fixture
def fake_app(tmp_path):
    """웹앱처럼 생긴 최소 폴더."""
    root = tmp_path / "아이패드웹앱"
    (root / "wheels").mkdir(parents=True)      # 빌드된 표시
    (root / "index.html").write_text("<p>화면</p>", encoding="utf-8")
    (root / "engine.wasm").write_bytes(b"\0asm\x01\0\0\0")
    return root


@pytest.fixture
def running(fake_app):
    """실제로 도는 로컬 서버."""
    import threading

    server = localapp.serve(fake_app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class TestFindingTheFolder:
    def test_env_var_wins(self, fake_app, monkeypatch) -> None:
        monkeypatch.setenv("PENSION_WEBAPP", str(fake_app))
        assert localapp.app_root() == fake_app

    def test_env_var_pointing_nowhere_says_so(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PENSION_WEBAPP", str(tmp_path / "없는곳"))
        with pytest.raises(localapp.MissingAppError, match="PENSION_WEBAPP"):
            localapp.app_root()

    def test_the_unbuilt_source_folder_is_not_mistaken_for_the_app(
        self, tmp_path, monkeypatch
    ) -> None:
        """webapp/app 에는 엔진이 없다. 띄우면 화면만 뜨고 아무것도 못 한다."""
        source = tmp_path / "웹앱"
        source.mkdir()
        (source / "index.html").write_text("<p>원본</p>", encoding="utf-8")
        monkeypatch.delenv("PENSION_WEBAPP", raising=False)
        monkeypatch.setattr(localapp, "_bases", lambda: [tmp_path])

        with pytest.raises(localapp.MissingAppError):
            localapp.app_root()

    def test_found_next_to_the_executable(self, fake_app, monkeypatch) -> None:
        """배포 꾸러미는 실행 파일 옆에 '아이패드웹앱' 폴더를 둔다."""
        monkeypatch.delenv("PENSION_WEBAPP", raising=False)
        monkeypatch.setattr(localapp, "_bases", lambda: [fake_app.parent])
        assert localapp.app_root() == fake_app

    def test_missing_folder_lists_where_it_looked(self, tmp_path, monkeypatch) -> None:
        """'못 찾았습니다' 만으로는 사용자가 할 수 있는 일이 없다."""
        monkeypatch.delenv("PENSION_WEBAPP", raising=False)
        monkeypatch.setattr(localapp, "_bases", lambda: [tmp_path])

        with pytest.raises(localapp.MissingAppError) as caught:
            localapp.app_root()
        message = str(caught.value)
        assert "같은 자리에" in message
        assert str(tmp_path) in message

    def test_the_real_dev_tree_is_reachable(self) -> None:
        """개발 중에는 webapp/dist 가 그대로 쓰여야 한다."""
        built = Path(localapp.__file__).resolve().parents[2] / "webapp" / "dist"
        if not (built / "index.html").is_file():
            pytest.skip("webapp/build.py 를 먼저 실행")
        assert localapp.app_root() == built


class TestServing:
    def test_serves_the_page(self, running) -> None:
        with urllib.request.urlopen(f"{running}/index.html") as response:
            assert response.status == 200
            assert "화면" in response.read().decode("utf-8")

    def test_wasm_gets_the_right_type(self, running) -> None:
        """application/octet-stream 으로 나가면 브라우저가 엔진을 못 띄운다."""
        with urllib.request.urlopen(f"{running}/engine.wasm") as response:
            assert response.headers["Content-Type"] == "application/wasm"

    def test_nothing_is_cached(self, running) -> None:
        """새 배포본을 덮어썼는데 옛 화면이 뜨면 원인을 찾기 어렵다."""
        with urllib.request.urlopen(f"{running}/index.html") as response:
            assert response.headers["Cache-Control"] == "no-cache"

    def test_listens_only_on_this_pc(self, fake_app) -> None:
        """인증이 없는 화면이다. 사내망에도 저절로 열리면 안 된다."""
        server = localapp.serve(fake_app, port=0)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()


class TestStablePort:
    """늘 같은 포트로 열어야 한다.

    브라우저 저장소는 **주소마다 따로** 다. 포트도 주소의 일부라, 열 때마다
    빈 포트를 새로 고르면 어제 저장한 산출 내역이 오늘 안 보인다 — 자료가
    지워진 것이 아니라 다른 주소를 열고 있는 것인데, 쓰는 사람에게는 사라진
    것과 같다.
    """

    def test_uses_the_fixed_port(self, fake_app) -> None:
        server = localapp.serve(fake_app)
        try:
            assert server.server_address[1] == localapp.DEFAULT_PORT
        finally:
            server.server_close()

    def test_the_same_port_comes_back_next_time(self, fake_app) -> None:
        """창을 닫았다 다시 열어도 같은 주소여야 저장소가 이어진다."""
        first = localapp.serve(fake_app)
        port = first.server_address[1]
        first.server_close()

        second = localapp.serve(fake_app)
        try:
            assert second.server_address[1] == port
        finally:
            second.server_close()

    def test_steps_aside_when_the_port_is_taken(self, fake_app) -> None:
        """이미 쓰이고 있으면 다음 칸으로. 두 창이 같은 포트를 잡으면 요청을
        서로 가로챈다 — 윈도우의 ``SO_REUSEADDR`` 이 실제로 그렇게 동작해서
        여기서 걸린 적이 있다.
        """
        held = localapp.serve(fake_app)
        try:
            other = localapp.serve(fake_app)
            try:
                assert other.server_address[1] in localapp.PORT_LADDER
                assert other.server_address[1] != held.server_address[1]
            finally:
                other.server_close()
        finally:
            held.server_close()

    def test_a_second_bind_on_the_same_port_is_refused(self, fake_app) -> None:
        """포트를 콕 집어 달라고 했는데 이미 쓰이고 있으면 거절되어야 한다.

        여기서 조용히 성공하면 두 창이 같은 포트를 나눠 갖는다.
        """
        held = localapp.serve(fake_app)
        port = held.server_address[1]
        try:
            with pytest.raises(OSError):
                localapp.serve(fake_app, port=port).server_close()
        finally:
            held.server_close()

    def test_the_ladder_stays_narrow(self) -> None:
        """넓게 흩어질수록 저장소가 갈린다. 사다리는 짧아야 한다."""
        assert localapp.PORT_LADDER[0] == localapp.DEFAULT_PORT
        assert len(localapp.PORT_LADDER) <= 10

    def test_it_does_not_collide_with_the_intranet_server(self) -> None:
        """`pension web` 은 8035 를 쓴다. 둘을 같이 띄우는 사람이 있다."""
        from pension.web import serve as web_serve

        import inspect

        default = inspect.signature(web_serve).parameters["port"].default
        assert default not in localapp.PORT_LADDER

    def test_files_outside_the_folder_are_not_served(self, running, tmp_path) -> None:
        (tmp_path / "secret.txt").write_text("명부", encoding="utf-8")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{running}/../secret.txt")
        assert caught.value.code == 404


class TestAppWindow:
    """브라우저 탭이 아니라 주소창 없는 앱 창으로 띄운다.

    탭으로 열리면 즐겨찾기·다른 탭 사이에 섞여 '프로그램' 으로 보이지 않는다.
    """

    def test_no_app_browser_off_windows(self, monkeypatch) -> None:
        monkeypatch.setattr(localapp.sys, "platform", "linux")
        assert localapp._app_browser() == ""

    def test_finds_edge_where_windows_puts_it(self, tmp_path, monkeypatch) -> None:
        edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        edge.parent.mkdir(parents=True)
        edge.write_text("", encoding="utf-8")

        monkeypatch.setattr(localapp.sys, "platform", "win32")
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "없음"))
        assert localapp._app_browser() == str(edge)

    def test_falls_back_when_no_browser_is_found(self, tmp_path, monkeypatch) -> None:
        """엣지가 없는 PC 도 있다. 화면이 아예 안 뜨는 것보다 탭이 낫다."""
        monkeypatch.setattr(localapp.sys, "platform", "win32")
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "없음"))
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "없음2"))
        assert localapp._app_browser() == ""

    def test_opens_as_an_app_window(self, fake_app, monkeypatch) -> None:
        """`--app=` 이 빠지면 그냥 탭으로 열린다. 인자를 지켜본다."""
        import subprocess

        seen = {}
        monkeypatch.setattr(localapp, "_app_browser", lambda: "msedge.exe")
        monkeypatch.setattr(subprocess, "Popen",
                            lambda cmd, **kw: seen.setdefault("cmd", cmd))

        server, url = localapp.open_in_browser(fake_app, port=0)
        try:
            assert seen["cmd"][0] == "msedge.exe"
            assert seen["cmd"][1] == f"--app={url}"
            # 전용 프로필을 만들면 저장해 둔 산출 내역이 안 보인다.
            assert not any(a.startswith("--user-data-dir") for a in seen["cmd"])
        finally:
            server.shutdown()
            server.server_close()

    def test_uses_the_default_browser_when_the_app_window_fails(
        self, fake_app, monkeypatch
    ) -> None:
        import subprocess
        import webbrowser

        opened = {}
        monkeypatch.setattr(localapp, "_app_browser", lambda: "msedge.exe")
        monkeypatch.setattr(subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("못 띄움")))
        monkeypatch.setattr(webbrowser, "open",
                            lambda link: opened.setdefault("url", link))

        server, url = localapp.open_in_browser(fake_app, port=0)
        try:
            assert opened["url"] == url
        finally:
            server.shutdown()
            server.server_close()
