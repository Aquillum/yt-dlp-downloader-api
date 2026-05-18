import unittest

from app.downloader import build_transcript_markdown, caption_payload_to_text, format_duration, select_caption_track


class CaptionSelectionTests(unittest.TestCase):
    def test_select_caption_track_prefers_video_language_then_matching_subtitles(self):
        info = {
            'language': 'de-DE',
            'subtitles': {
                'en': [{'ext': 'vtt', 'url': 'https://example/en.vtt'}],
                'de': [{'ext': 'vtt', 'url': 'https://example/de.vtt'}],
            },
            'automatic_captions': {
                'en': [{'ext': 'vtt', 'url': 'https://example/auto-en.vtt'}],
            },
        }

        track = select_caption_track(info, preferred_langs=['en', 'en-US'])

        self.assertIsNotNone(track)
        self.assertEqual(track['language'], 'de')
        self.assertEqual(track['source'], 'subtitles')
        self.assertTrue(track['format']['url'].endswith('/de.vtt'))

    def test_select_caption_track_prefers_manual_subtitles_before_auto_captions(self):
        info = {
            'language': 'en',
            'subtitles': {
                'en-GB': [{'ext': 'vtt', 'url': 'https://example/manual-en-gb.vtt'}],
            },
            'automatic_captions': {
                'en': [{'ext': 'vtt', 'url': 'https://example/auto-en.vtt'}],
            },
        }

        track = select_caption_track(info, preferred_langs=['en', 'en-US', 'en-GB'])

        self.assertIsNotNone(track)
        self.assertEqual(track['language'], 'en-GB')
        self.assertEqual(track['source'], 'subtitles')

    def test_caption_payload_to_text_cleans_vtt_timestamps_tags_and_duplicates(self):
        payload = '''WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<v Speaker>Welcome &amp; hello</v>\n\n00:00:02.000 --> 00:00:03.000\nWelcome &amp; hello\n\n00:00:03.000 --> 00:00:04.000\nNext line.\n'''

        self.assertEqual(caption_payload_to_text(payload, 'vtt'), 'Welcome & hello Next line.')

    def test_caption_payload_to_text_reads_youtube_json3(self):
        payload = '{"events":[{"segs":[{"utf8":"Hello "},{"utf8":"world"}]},{"segs":[{"utf8":"\\n"}]},{"segs":[{"utf8":"Again"}]}]}'

        self.assertEqual(caption_payload_to_text(payload, 'json3'), 'Hello world Again')

    def test_format_duration_prints_hms(self):
        self.assertEqual(format_duration(65), '00:01:05')
        self.assertEqual(format_duration(3661), '01:01:01')
        self.assertEqual(format_duration(None), 'unknown')

    def test_build_transcript_markdown_includes_video_metadata_header(self):
        info = {
            'title': 'Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans',
            'channel': 'Wanderloots',
            'duration': 929,
        }

        md = build_transcript_markdown(
            source_url='https://www.youtube.com/watch?v=n4EVksU_EOs',
            info=info,
            engine='yt-dlp automatic_captions',
            language='en-orig',
            raw_captions='n4EVksU_EOs.en-orig.automatic_captions.json3',
            text='Transcript body.',
        )

        self.assertIn('# Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans\n', md)
        self.assertIn('Source: https://www.youtube.com/watch?v=n4EVksU_EOs\n', md)
        self.assertIn('Title: Why LLM Wiki? 🧠 Future Of Knowledge For Agentic AI & Humans\n', md)
        self.assertIn('Channel: Wanderloots\n', md)
        self.assertIn('Duration: 00:15:29\n', md)
        self.assertIn('Engine: yt-dlp automatic_captions\n', md)
        self.assertIn('Language: en-orig\n', md)
        self.assertIn('Raw captions: n4EVksU_EOs.en-orig.automatic_captions.json3\n', md)
        self.assertTrue(md.endswith('## Text\n\nTranscript body.\n'))


if __name__ == '__main__':
    unittest.main()
