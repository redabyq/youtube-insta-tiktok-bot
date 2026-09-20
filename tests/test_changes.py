import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bot'))
sys.path.insert(0, str(ROOT))
import downloader
import compose


class DownloadTests(unittest.TestCase):
    def test_direct_mode_ignores_environment_proxy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(downloader.config, 'PROXY_URL', None), patch.object(downloader.config, 'COOKIES_FILE', ''):
            self.assertEqual(downloader._base_opts(tmp)['proxy'], '')

    def test_cookie_source_is_not_modified_or_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.txt'
            source.write_text('# Netscape HTTP Cookie File\n')
            output = Path(tmp) / 'output'
            output.mkdir()
            with patch.object(downloader.config, 'COOKIES_FILE', str(source)):
                opts = downloader._base_opts(str(output))
            self.assertNotEqual(opts['cookiefile'], str(source))
            self.assertEqual(Path(opts['cookiefile']).read_text(), source.read_text())

    def test_failure_cleans_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'request'
            output.mkdir()
            with patch.object(downloader.tempfile, 'mkdtemp', return_value=str(output)), patch.object(downloader, '_run_download', side_effect=RuntimeError('This video is not available')):
                with self.assertRaises(downloader.DownloadError):
                    downloader.download('https://youtu.be/example', 'mp3')
            self.assertFalse(output.exists())

    def test_only_audio_returned_even_with_larger_thumbnail(self):
        def fake_download(opts, url):
            folder = Path(opts['outtmpl']).parent
            (folder / 'song.mp3').write_bytes(b'audio')
            (folder / 'thumbnail.jpg').write_bytes(b'x' * 1000)
        with patch.object(downloader, '_run_download', side_effect=fake_download), patch.object(downloader, '_add_cover'):
            result = downloader.download('https://youtu.be/example', 'mp3')
            try:
                self.assertTrue(result.endswith('.mp3'))
            finally:
                shutil.rmtree(Path(result).parent)

    def test_error_messages_are_actionable(self):
        for raw in ('This video is not available', 'Instagram sent an empty media response', "Sign in to confirm you’re not a bot"):
            message = downloader.friendly_error(RuntimeError(raw))
            self.assertNotIn('ERROR:', message)
            self.assertNotEqual(raw, message)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_real_cover_embedding_and_failed_cover(self):
        from mutagen.id3 import ID3
        with tempfile.TemporaryDirectory() as tmp:
            audio = str(Path(tmp) / 'audio.mp3')
            image = str(Path(tmp) / 'thumbnail.png')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc', '-t', '0.1', audio], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=640x360', '-frames:v', '1', image], check=True)
            downloader._add_cover(audio, tmp)
            self.assertEqual(len(ID3(audio).getall('APIC')), 1)
            self.assertLess((Path(tmp) / 'cover.jpg').stat().st_size, 200 * 1024)
            with patch.object(downloader.subprocess, 'run', side_effect=OSError('no ffmpeg')):
                downloader._add_cover(audio, tmp)
            self.assertTrue(Path(audio).is_file())


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose unavailable')
class ComposeTests(unittest.TestCase):
    def test_all_modes(self):
        run = subprocess.run
        for proxy in ('0', 'false', '1', 'true'):
            for local in ('true', 'false'):
                with self.subTest(proxy=proxy, local=local), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    for name in ('docker-compose.yml', 'docker-compose.proxy.yml'):
                        shutil.copyfile(ROOT / name, root / name)
                    (root / '.env').write_text(f'PROXY_ENABLED={proxy}\nUSE_LOCAL_BOT_API={local}\n')
                    captured = {}
                    def intercept(args, **kwargs):
                        if 'input' in kwargs:
                            captured.update(json.loads(kwargs['input']))
                            self.assertIn('--remove-orphans', args)
                            return subprocess.CompletedProcess(args, 0)
                        return run(args, **kwargs)
                    with patch.object(compose, 'ROOT', root), patch.object(compose.sys, 'argv', ['compose.py', 'up', '-d']), patch.object(compose.subprocess, 'run', side_effect=intercept), patch.dict(os.environ, {'PROXY_ENABLED': proxy, 'USE_LOCAL_BOT_API': local}):
                        # Exercise .env parsing, not just inherited shell vars.
                        os.environ.pop('PROXY_ENABLED')
                        os.environ.pop('USE_LOCAL_BOT_API')
                        self.assertEqual(compose.main(), 0)
                    services = captured['services']
                    enabled = proxy in ('1', 'true')
                    self.assertEqual('xray' in services, enabled)
                    self.assertEqual('tun2socks' in services, enabled and local == 'true')
                    self.assertEqual('telegram-bot-api' in services, local == 'true')
                    for service in services.values():
                        self.assertTrue(set(service.get('depends_on', {})) <= set(services))


if __name__ == '__main__':
    unittest.main()
