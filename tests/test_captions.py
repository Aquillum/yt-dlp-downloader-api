import unittest

from app.downloader import caption_payload_to_text, select_caption_track


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


if __name__ == '__main__':
    unittest.main()
