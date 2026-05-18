import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'download_and_transcribe.py'
spec = importlib.util.spec_from_file_location('download_and_transcribe', SCRIPT)
assert spec and spec.loader
download_and_transcribe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download_and_transcribe)


class ScriptHelperTests(unittest.TestCase):
    def test_normalize_title_filename_slugifies_youtube_title(self):
        self.assertEqual(
            download_and_transcribe.normalized_title_filename('Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans'),
            'why-llm-wiki-future-of-knowledge-for-agentic-ai-humans',
        )

    def test_video_print_line_matches_yt_dlp_print_request(self):
        info = {
            'title': 'Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans',
            'channel': 'Wanderloots',
            'duration': 929,
        }

        self.assertEqual(
            download_and_transcribe.video_print_line(info),
            'Wanderloots - 929 - Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans',
        )


if __name__ == '__main__':
    unittest.main()
