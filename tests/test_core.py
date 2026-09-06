import copy
import json

import pytest

from koeminer.core import Corpus, Mapping, Sentence, Settings, enrich


def test_enrichment_preserves_yomitan_and_does_not_mutate():
    original = {"fields": {"Expression": "猫", "Sentence": "old", "SentenceAudio": "old audio", "Meaning": "cat"},
                "tags": ["yomitan"], "audio": [{"filename": "word.mp3", "fields": ["Meaning", "SentenceAudio"]}]}
    before = copy.deepcopy(original)
    result = enrich(original, Mapping(), Sentence("猫 & 犬", "", "", "a.mp3"), "selected.mp3")
    assert original == before
    assert result["fields"]["Meaning"] == "cat"
    assert result["fields"]["Sentence"] == "猫 &amp; 犬"
    assert result["fields"]["SentenceAudio"] == "[sound:selected.mp3]"
    assert result["audio"][0]["fields"] == ["Meaning"]
    assert result["tags"] == ["yomitan"]


def test_append():
    result = enrich({"fields": {"Sentence": "old", "SentenceAudio": "[sound:old.mp3]"}},
                    Mapping(append=True), Sentence("猫", "", "", "a.mp3"), "new.mp3")
    assert result["fields"]["SentenceAudio"] == "[sound:old.mp3]<br>[sound:new.mp3]"


def test_missing_field_is_error():
    with pytest.raises(ValueError, match="нет поля"):
        enrich({"fields": {}}, Mapping(), Sentence("猫", "", "", "a.mp3"), "a.mp3")


def test_mapping_rejects_overwriting_word():
    with pytest.raises(ValueError):
        Mapping(sentence="Expression").validate()


def test_profiles_roundtrip_and_loop_prevention(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(profiles={"日本語": Mapping()})
    settings.save(path)
    assert Settings.load(path) == settings
    settings.port = 8765
    with pytest.raises(ValueError):
        settings.validate()


def test_search_literal_and_sort(tmp_path):
    path = tmp_path / "sentences.json"
    path.write_text(json.dumps([
        {"jap": "猫が好きです", "eng": "I like cats", "audio_jap": "a.mp3"},
        {"jap": "猫", "eng": "cat", "audio_jap": "b.mp3"},
    ]), encoding="utf-8")
    corpus = Corpus(path)
    assert [x.japanese for x in corpus.search("猫")] == ["猫", "猫が好きです"]
    assert corpus.search("[") == []
    assert corpus.search("") == []


def test_audio_host_cannot_be_replaced():
    with pytest.raises(ValueError):
        _ = Sentence("", "", "", "https://evil.test/file").audio_url
