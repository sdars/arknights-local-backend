"""Small event domains: gallery, fireworks, invites, Siracusa, retro and ODC."""

from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

from .. import battles, game_data
from ..config import DATA_DIR
from ..db import connect, now_ts
from ..feature_common import (
    apply_items,
    as_bool,
    as_dict,
    as_int,
    auth_error,
    battle_id,
    body_id,
    call,
    ensure_dict,
    ensure_list,
    fail,
    finish,
    first_value,
    merge_delta,
    unique_append,
)


router = APIRouter()

THUMBNAIL_DIR = DATA_DIR / "gallery_thumbnails"
THUMBNAIL_SIZE = (640, 360)
THUMBNAIL_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


@lru_cache(maxsize=24)
def _thumbnail_font(size: int):
    try:
        return ImageFont.truetype(THUMBNAIL_FONT, size)
    except Exception:
        return ImageFont.load_default()


def _fit_text(draw, text: str, font, max_width: int) -> str:
    text = str(text or "")
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return (text + suffix) if text else suffix


def _gallery_thumbnail_state(uid: str, magazine_id: str):
    """Read the magazine and owner display data for a thumbnail."""
    magazine = {}
    user = {}
    if not str(uid or "").isdigit():
        return magazine, user
    with connect() as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    if not row:
        return magazine, user
    try:
        user = json.loads(row["user_json"])
    except (TypeError, ValueError):
        user = {}
    gallery = user.get("gallery") if isinstance(user, dict) else None
    magazines = gallery.get("magazines") if isinstance(gallery, dict) else None
    if isinstance(magazines, dict) and isinstance(magazines.get(str(magazine_id)), dict):
        magazine = magazines[str(magazine_id)]
    return magazine, user


def _render_gallery_thumbnail(uid: str, magazine_id: str, magazine: dict, user: dict) -> bytes:
    """Render a real local PNG from the saved magazine state.

    The client downloads the returned URL as a texture.  A generated cover is
    deterministic, account-specific and never points at a missing static file.
    """
    seed = hashlib.sha256(f"{uid}:{magazine_id}".encode("utf-8")).digest()
    top = (25 + seed[0] % 45, 35 + seed[1] % 45, 70 + seed[2] % 55)
    bottom = (8 + seed[3] % 20, 12 + seed[4] % 20, 26 + seed[5] % 25)
    width, height = THUMBNAIL_SIZE
    image = Image.new("RGB", THUMBNAIL_SIZE, bottom)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    # A restrained editorial grid and accent block keep the cover legible
    # even when a magazine has no custom artwork in its save payload.
    accent = (190, 155, 85)
    draw.line((44, 48, width - 44, 48), fill=(255, 255, 255, 55), width=1)
    draw.line((44, 48, 44, height - 52), fill=accent, width=3)
    for index in range(4):
        x = 64 + index * 34
        draw.line((x, height - 62, x + 22, height - 62), fill=(210, 190, 140, 150), width=2)
    draw.ellipse((width - 150, 62, width - 74, 138), outline=(220, 190, 120, 120), width=2)
    draw.ellipse((width - 133, 79, width - 91, 121), outline=(220, 190, 120, 100), width=1)

    title = str(magazine.get("name") or magazine.get("title") or magazine_id or "未命名杂志")
    nickname = str(((user.get("status") or {}).get("nickName") if isinstance(user, dict) else "") or "博士")
    title_font = _thumbnail_font(34)
    body_font = _thumbnail_font(18)
    small_font = _thumbnail_font(14)
    title = _fit_text(draw, title, title_font, width - 118)
    draw.text((64, 92), title, font=title_font, fill=(246, 240, 224))
    draw.text((66, 148), "ART MAGAZINE · LOCAL COLLECTION", font=small_font, fill=(208, 184, 125))
    draw.text((66, height - 104), _fit_text(draw, f"{nickname}  /  UID {uid or '-'}", body_font, width - 130), font=body_font, fill=(220, 224, 234))

    slots = magazine.get("slots") if isinstance(magazine.get("slots"), list) else []
    card_y = 190
    for index, raw in enumerate(slots[:4]):
        if not isinstance(raw, dict):
            continue
        char_id = str(raw.get("charId") or raw.get("charInstId") or "")
        meta = game_data.character(char_id) or {}
        label = str(meta.get("name") or char_id or f"SLOT {index + 1}")
        x = 64 + index * 135
        draw.rounded_rectangle((x, card_y, x + 118, card_y + 52), radius=8, fill=(15, 18, 28), outline=(190, 160, 90), width=1)
        draw.text((x + 10, card_y + 10), _fit_text(draw, label, body_font, 96), font=body_font, fill=(238, 226, 192))
        draw.text((x + 10, card_y + 31), f"SLOT {index + 1}", font=small_font, fill=(160, 165, 180))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _thumbnail_bytes(uid: str, magazine_id: str) -> bytes:
    magazine, user = _gallery_thumbnail_state(uid, magazine_id)
    seed = json.dumps(
        {"uid": str(uid or ""), "id": str(magazine_id or ""), "magazine": magazine},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()[:20]
    safe_uid = str(uid or "public") if str(uid or "").isdigit() else "public"
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    path = THUMBNAIL_DIR / f"{safe_uid}_{digest}.png"
    if path.is_file():
        return path.read_bytes()
    data = _render_gallery_thumbnail(str(uid or ""), str(magazine_id or ""), magazine, user)
    path.write_bytes(data)
    return data


def _reward(call_obj, suffix: str) -> list[dict]:
    """Deterministically grant a small local reward for a claimed milestone."""
    amount = 100 + (sum(str(suffix).encode("utf-8")) % 5) * 100
    return apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": amount}], f"feature:{suffix}")


# ── CGGallery ────────────────────────────────────────────────────────────────


@router.post("/gallery/changeMagazineSquad")
async def gallery_change_magazine_squad(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    gallery = ensure_dict(call_obj.user, "gallery")
    squad = first_value(call_obj.body, "squad", "squads", default=[])
    gallery["magazineSquad"] = squad if isinstance(squad, list) else []
    return finish(call_obj, "gallery")


@router.post("/gallery/getCollectionRewards")
async def gallery_get_collection_rewards(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    set_id = body_id(call_obj.body, "setId", "groupId")
    mission_id = body_id(call_obj.body, "missionId", "id")
    key = f"{set_id}:{mission_id}"
    gallery = ensure_dict(call_obj.user, "gallery")
    claimed = ensure_dict(gallery, "collectionRewards")
    if claimed.get(key):
        return fail("gallery reward already claimed")
    claimed[key] = 1
    items = _reward(call_obj, "gallery:" + key)
    call_obj.extra["rewards"] = items
    return finish(call_obj, "gallery")


@router.post("/gallery/getFirstRewards")
async def gallery_get_first_rewards(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    gallery = ensure_dict(call_obj.user, "gallery")
    leaf = ensure_dict(gallery, "leafMap")
    first_key = "first"
    if leaf.get(first_key):
        return fail("gallery first reward already claimed")
    leaf[first_key] = 1
    rewards = _reward(call_obj, "gallery:first")
    call_obj.extra["rewards"] = rewards
    return finish(call_obj, "gallery")


@router.post("/gallery/getThumbnailUrl")
async def gallery_get_thumbnail_url(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    ids = call_obj.body.get("idList")
    ids = ids if isinstance(ids, list) else []
    uid = str(call_obj.account.get("uid") or "")
    return {
        "result": 0,
        "url": {
            str(item): f"/gallery/thumbnail.png?magazineId={quote(str(item), safe='')}&uid={quote(uid)}"
            for item in ids
            if str(item)
        },
        "playerDataDelta": {"deleted": {}, "modified": {"gallery": ensure_dict(call_obj.user, "gallery")}},
    }


@router.get("/gallery/thumbnail.png")
async def gallery_thumbnail(magazineId: str = "", uid: str = ""):
    """Serve the PNG generated for a saved DIY magazine."""
    data = _thumbnail_bytes(uid, magazineId)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _save_magazine(call_obj, version: int):
    gallery = ensure_dict(call_obj.user, "gallery")
    magazines = ensure_dict(gallery, "magazines")
    magazine = first_value(call_obj.body, "magazine", "data", default={})
    magazine = as_dict(magazine)
    magazine_id = str(magazine.get("id") or magazine.get("magazineId") or f"diy_{version}_{now_ts()}")
    magazine["id"] = magazine_id
    magazine["version"] = version
    magazines[magazine_id] = magazine
    return finish(call_obj, "gallery")


@router.post("/gallery/saveDiyMagazineV1")
async def gallery_save_diy_magazine_v1(request: Request):
    call_obj = await call(request)
    return _save_magazine(call_obj, 1) if call_obj else auth_error()


@router.post("/gallery/saveDiyMagazineV2")
async def gallery_save_diy_magazine_v2(request: Request):
    call_obj = await call(request)
    return _save_magazine(call_obj, 2) if call_obj else auth_error()


# ── Firework ─────────────────────────────────────────────────────────────────


@router.post("/firework/changeAnimal")
async def firework_change_animal(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    animal = body_id(call_obj.body, "animal", "animalId")
    root = ensure_dict(call_obj.user, "firework")
    node = ensure_dict(root, "animal")
    unlocked = ensure_dict(node, "unlock")
    if animal and animal not in unlocked:
        return fail("animal not unlocked")
    node["select"] = animal
    return finish(call_obj, "firework")


@router.post("/firework/savePlateSlots")
async def firework_save_plate_slots(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    slots = call_obj.body.get("slots")
    plate = ensure_dict(ensure_dict(call_obj.user, "firework"), "plate")
    plate["slots"] = slots if isinstance(slots, list) else []
    return finish(call_obj, "firework")


# ── Invite ───────────────────────────────────────────────────────────────────


def _invite_state(call_obj):
    root = ensure_dict(call_obj.user, "invite")
    activity = ensure_dict(root, "ACTIVITY")
    return root, activity


@router.post("/invite/sendInvite")
async def invite_send(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, activity = _invite_state(call_obj)
    invite_id = body_id(call_obj.body, "inviteId", "id", default=f"invite_{now_ts()}")
    uid = body_id(call_obj.body, "uid", "targetUid", default="")
    node = ensure_dict(activity, invite_id)
    sent = ensure_list(node, "sent")
    if uid:
        unique_append(sent, uid)
    node["createdTs"] = now_ts()
    return finish(call_obj, "invite")


@router.post("/invite/refreshInviteList")
async def invite_refresh(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, activity = _invite_state(call_obj)
    invite_id = body_id(call_obj.body, "inviteId", "id")
    node = ensure_dict(activity, invite_id)
    entries = ensure_list(node, "accepted")
    return {
        "result": 0,
        "inviteList": [{"uid": uid} for uid in entries],
        "playerDataDelta": {"deleted": {}, "modified": {"invite": root}},
    }


@router.post("/invite/processInvite")
async def invite_process(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, activity = _invite_state(call_obj)
    invite_id = body_id(call_obj.body, "inviteId", "id")
    uid = body_id(call_obj.body, "fromUid", "uid")
    operate = as_int(call_obj.body.get("operate"), 0)
    node = ensure_dict(activity, invite_id)
    if uid and operate == 1:
        unique_append(ensure_list(node, "rejected"), uid)
    elif uid:
        unique_append(ensure_list(node, "accepted"), uid)
    return finish(call_obj, "invite")


@router.post("/invite/switchInviteAccept")
async def invite_switch_accept(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, activity = _invite_state(call_obj)
    invite_id = body_id(call_obj.body, "inviteId", "id", default="default")
    node = ensure_dict(activity, invite_id)
    node["accept"] = as_int(call_obj.body.get("operate"), 0) != 1
    return finish(call_obj, "invite")


# ── Siracusa ─────────────────────────────────────────────────────────────────


@router.post("/siracusaMap/avgOptionSelect")
async def siracusa_avg_option_select(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "siracusaMap")
    task_id = body_id(call_obj.body, "taskId", "id", default="task")
    node = ensure_dict(ensure_dict(root, "card"), task_id)
    node["optionId"] = body_id(call_obj.body, "optionId")
    node["groupId"] = body_id(call_obj.body, "groupId")
    return finish(call_obj, "siracusaMap")


@router.post("/siracusaMap/avgItemCardGain")
async def siracusa_avg_item_card_gain(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "siracusaMap")
    card_id = body_id(call_obj.body, "itemCardId", "cardId")
    if card_id:
        ensure_dict(ensure_dict(root, "card"), card_id)["unlock"] = 1
    return finish(call_obj, "siracusaMap")


@router.post("/siracusaMap/avgTaskFinish")
async def siracusa_avg_task_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "siracusaMap")
    task_id = body_id(call_obj.body, "taskId", "id", default="task")
    node = ensure_dict(ensure_dict(root, "card"), task_id)
    node["state"] = 2
    node["finishTs"] = now_ts()
    return finish(call_obj, "siracusaMap")


@router.post("/siracusaMap/cardSelect")
async def siracusa_card_select(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "siracusaMap")
    card = ensure_dict(root, "card")
    card_id = body_id(call_obj.body, "cardId", "id")
    if not card_id:
        return fail("cardId required")
    card["selected"] = card_id
    ensure_dict(card, card_id)["selected"] = 1
    return finish(call_obj, "siracusaMap")


@router.post("/siracusaMap/operaCommentLike")
async def siracusa_opera_comment_like(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    opera = ensure_dict(ensure_dict(call_obj.user, "siracusaMap"), "opera")
    comment_id = body_id(call_obj.body, "commentId", "id")
    if not comment_id:
        return fail("commentId required")
    ensure_dict(opera, "like")[comment_id] = 1
    return finish(call_obj, "siracusaMap")


@router.post("/siracusaMap/taskRingGainReward")
async def siracusa_task_ring_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    ring_id = body_id(call_obj.body, "taskRingId", "id")
    root = ensure_dict(call_obj.user, "siracusaMap")
    claimed = ensure_dict(root, "taskRingRewards")
    if not ring_id or claimed.get(ring_id):
        return fail("invalid or already claimed task ring")
    claimed[ring_id] = 1
    items = _reward(call_obj, "siracusa:" + ring_id)
    call_obj.extra["items"] = items
    return finish(call_obj, "siracusaMap")


# ── Retro ────────────────────────────────────────────────────────────────────


@router.post("/retro/getRetroPassReward")
async def retro_get_pass_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    retro_id = body_id(call_obj.body, "retroId", "id")
    activity_id = body_id(call_obj.body, "activityId", "activity")
    root = ensure_dict(call_obj.user, "retro")
    claimed = ensure_dict(root, "passRewards")
    key = f"{retro_id}:{activity_id}"
    if not retro_id or claimed.get(key):
        return fail("retro pass reward unavailable")
    claimed[key] = 1
    items = _reward(call_obj, "retro-pass:" + key)
    call_obj.extra["items"] = items
    return finish(call_obj, "retro")


@router.post("/retro/getRetroTrailReward")
async def retro_get_trail_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    retro_id = body_id(call_obj.body, "retroId", "id")
    reward_id = body_id(call_obj.body, "rewardId", "reward")
    trail = ensure_dict(ensure_dict(call_obj.user, "retro"), "trail")
    group = ensure_dict(trail, retro_id)
    if not retro_id or not reward_id or as_int(group.get(reward_id), 0) == 1:
        return fail("retro trail reward unavailable")
    group[reward_id] = 1
    items = _reward(call_obj, "retro-trail:" + reward_id)
    call_obj.extra["items"] = items
    return finish(call_obj, "retro")


@router.post("/retro/unlockRetroBlock")
async def retro_unlock_block(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    retro_id = body_id(call_obj.body, "retroId", "id")
    block = ensure_dict(ensure_dict(call_obj.user, "retro"), "block")
    node = ensure_dict(block, retro_id)
    node["locked"] = 0
    node["open"] = 1
    return finish(call_obj, "retro")


@router.post("/retro/typeAct20side/competitionStart")
async def retro_act20_competition_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "retro")
    comp = ensure_dict(root, "act20side")
    comp["state"] = "RUNNING"
    comp["startTs"] = now_ts()
    return finish(call_obj, "retro")


@router.post("/retro/typeAct20side/competitionFinish")
async def retro_act20_competition_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "retro")
    comp = ensure_dict(root, "act20side")
    comp["state"] = "FINISH"
    comp["score"] = max(1, as_int(call_obj.body.get("score"), 1))
    comp["finishTs"] = now_ts()
    return finish(call_obj, "retro")


# ── VecBreak season records ─────────────────────────────────────────────────


@router.post("/vecBreakV2/getSeasonRecord")
async def vec_break_get_season_record(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    activity = ensure_dict(call_obj.user, "activity")
    record = ensure_dict(ensure_dict(activity, "VEC_BREAK_V2"), "act3break")
    return {
        "result": 0,
        "record": record.get("record") or {},
        "playerDataDelta": {"deleted": {}, "modified": {"activity": activity}},
    }


# ── ArkOdc ───────────────────────────────────────────────────────────────────


def _odc_topic(call_obj):
    root = ensure_dict(call_obj.user, "arkodc")
    topics = ensure_dict(root, "topics")
    topic_id = body_id(call_obj.body, "topicId", "topic", default="default")
    return root, ensure_dict(topics, topic_id)


@router.post("/arkodc/triggerInteraction")
async def arkodc_trigger_interaction(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, topic = _odc_topic(call_obj)
    interaction_id = body_id(call_obj.body, "operationId", "interactionId", "id")
    if interaction_id:
        unique_append(ensure_list(topic, "interactions"), interaction_id)
    topic["actorId"] = body_id(call_obj.body, "actorId")
    topic["avgId"] = body_id(call_obj.body, "avgId")
    award_id = body_id(call_obj.body, "awardId")
    if award_id:
        claimed = ensure_dict(topic, "awards")
        if not claimed.get(award_id):
            claimed[award_id] = 1
            call_obj.extra["items"] = _reward(call_obj, "arkodc:" + award_id)
    return finish(call_obj, "arkodc")


@router.post("/arkodc/restart")
async def arkodc_restart(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, topic = _odc_topic(call_obj)
    topic["position"] = {"x": 0, "y": 0, "z": 0}
    topic["state"] = "READY"
    topic["restartTs"] = now_ts()
    return finish(call_obj, "arkodc")


@router.post("/arkodc/savePosition")
async def arkodc_save_position(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, topic = _odc_topic(call_obj)
    topic["position"] = {
        "x": as_int(call_obj.body.get("x"), 0),
        "y": as_int(call_obj.body.get("y"), 0),
        "z": as_int(call_obj.body.get("z"), 0),
    }
    return finish(call_obj, "arkodc")


@router.post("/arkodc/battleStart")
async def arkodc_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, topic = _odc_topic(call_obj)
    topic["state"] = "BATTLE"
    topic["battleId"] = battle_id("arkodc", call_obj.account)
    return finish(call_obj, "arkodc")


@router.post("/arkodc/battleFinish")
async def arkodc_battle_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, topic = _odc_topic(call_obj)
    topic["state"] = "FINISH"
    topic["finishTs"] = now_ts()
    operation = body_id(call_obj.body, "operationId", "id")
    if operation:
        unique_append(ensure_list(topic, "operations"), operation)
    return finish(call_obj, "arkodc")


# ── Training ground ─────────────────────────────────────────────────────────


def _training_stage(call_obj, create=True):
    root = ensure_dict(call_obj.user, "trainingGround")
    stages = ensure_dict(root, "stages")
    stage_id = body_id(call_obj.body, "stageId", "id", default="ch_1")
    node = stages.get(stage_id)
    if not isinstance(node, dict) and create:
        node = {"stageId": stage_id, "state": 0, "rts": -1}
        stages[stage_id] = node
    return root, stages, stage_id, node if isinstance(node, dict) else None


@router.post("/trainingGround/battleStart")
async def training_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, stages, stage_id, node = _training_stage(call_obj)
    if not node:
        return fail("unknown training stage")
    node["state"] = 1
    node["startTs"] = now_ts()
    return finish(call_obj, "trainingGround")


@router.post("/trainingGround/battleFinish")
async def training_battle_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, stages, stage_id, node = _training_stage(call_obj)
    if not node:
        return fail("unknown training stage")
    node["state"] = 2
    node["rts"] = now_ts()
    node["score"] = max(0, as_int(call_obj.body.get("score"), 0))
    return finish(call_obj, "trainingGround")


# ── Recalibration rune ──────────────────────────────────────────────────────


@router.post("/rune/battleStart")
async def rune_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "recalRune")
    state = ensure_dict(root, "current")
    state["seasonId"] = body_id(call_obj.body, "seasonId", "season")
    state["stageId"] = body_id(call_obj.body, "stageId", "stage")
    state["runes"] = call_obj.body.get("runes") if isinstance(call_obj.body.get("runes"), list) else []
    state["slots"] = call_obj.body.get("slots") if isinstance(call_obj.body.get("slots"), list) else []
    state["state"] = "BATTLE"
    state["startTs"] = now_ts()
    return finish(call_obj, "recalRune")


@router.post("/rune/battleFinish")
async def rune_battle_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = ensure_dict(call_obj.user, "recalRune")
    state = ensure_dict(root, "current")
    score = max(0, as_int(call_obj.body.get("score"), 0))
    previous = as_int(state.get("score"), 0)
    state["score"] = max(previous, score)
    state["newRecord"] = score > previous
    state["state"] = "FINISH"
    state["finishTs"] = now_ts()
    seasons = ensure_dict(root, "seasons")
    season_id = str(state.get("seasonId") or "")
    if season_id:
        ensure_dict(seasons, season_id)["score"] = state["score"]
    return finish(call_obj, "recalRune")
