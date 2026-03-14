class TestRegister:
    async def test_register_team(self, async_client):
        async with async_client as c:
            resp = await c.post(
                "/register",
                json={"team_name": "Alpha"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_name"] == "Alpha"
        assert data["status"] == "registered"
        assert "token" in data


class TestTeams:
    async def test_list_teams(self, async_client, fake_redis):
        await fake_redis.hset("teams", "Alpha", "some-token")
        async with async_client as c:
            resp = await c.get("/teams")
        assert resp.status_code == 200
        teams = resp.json()["teams"]
        assert len(teams) == 1
        assert teams[0]["team_name"] == "Alpha"
        assert teams[0]["connected"] is False

    async def test_delete_team(self, async_client, fake_redis):
        await fake_redis.hset("teams", "Alpha", "some-token")
        async with async_client as c:
            resp = await c.delete("/teams/Alpha")
            assert resp.status_code == 200
            # Second delete should 404
            resp2 = await c.delete("/teams/Alpha")
            assert resp2.status_code == 404


class TestHealth:
    async def test_health_ok(self, async_client):
        async with async_client as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestGames:
    async def _register_teams(self, redis, count=5):
        for i in range(count):
            await redis.hset("teams", f"Team{i}", f"token-{i}")

    async def test_create_game(self, async_client, fake_redis):
        await self._register_teams(fake_redis, 5)
        async with async_client as c:
            resp = await c.post("/games", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["players"] == 5
        assert data["status"] == "created"

    async def test_create_game_too_few_teams(self, async_client, fake_redis):
        await self._register_teams(fake_redis, 3)
        async with async_client as c:
            resp = await c.post("/games", json={})
        assert resp.status_code == 400

    async def test_get_game_status(self, async_client, fake_redis):
        await self._register_teams(fake_redis, 5)
        async with async_client as c:
            create_resp = await c.post("/games", json={})
            game_id = create_resp.json()["game_id"]
            resp = await c.get(f"/games/{game_id}")
        assert resp.status_code == 200
        assert resp.json()["game_id"] == game_id
        assert resp.json()["phase"] == "lobby"

    async def test_get_game_not_found(self, async_client):
        async with async_client as c:
            resp = await c.get("/games/nonexistent")
        assert resp.status_code == 404


class TestScoreboard:
    async def test_empty_scoreboard(self, async_client):
        async with async_client as c:
            resp = await c.get("/scoreboard")
        assert resp.status_code == 200
        assert resp.json()["standings"] == []

    async def test_scoreboard_with_data(self, async_client, fake_redis):
        await fake_redis.zadd("scoreboard", {"Alpha": 10, "Beta": 5})
        async with async_client as c:
            resp = await c.get("/scoreboard")
        standings = resp.json()["standings"]
        assert len(standings) == 2
        assert standings[0]["team"] == "Alpha"
        assert standings[0]["score"] == 10
