#!/usr/bin/env python3
"""
Backend con Sesiones por Usuario y Descarga Directa al Cliente
- Cada usuario solo ve sus propias descargas
- Descargas directas al dispositivo del usuario (no al servidor)
- Soporte multi-plataforma (PC/móvil)
- Seguridad mejorada para producción
"""

import os
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import threading
import time
import uuid
import json
from datetime import datetime, timedelta
import yt_dlp
import logging
import re
from urllib.parse import urlparse

# Configurar logging (sin exponer datos sensibles)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuración de seguridad
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'https://videos.naperu.cloud,http://localhost:1005').split(',')
ALLOWED_DOMAINS = [
    'youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com',
    'instagram.com', 'www.instagram.com',
    'tiktok.com', 'www.tiktok.com', 'vm.tiktok.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'twitter.com', 'x.com', 'vimeo.com'
]
MAX_SESSIONS = 1000
SESSION_TIMEOUT_HOURS = 24

app = Flask(__name__, static_folder='frontend_sessions', static_url_path='')

# Configurar CORS - Restringido a dominios permitidos
CORS(app, 
     resources={r"/*": {"origins": ALLOWED_ORIGINS}},
     methods=["GET", "POST", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization", "X-Session-ID"]
)

def validate_url(url):
    """Validar que la URL sea de un dominio permitido"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remover puerto si existe
        domain = domain.split(':')[0]
        return any(domain == allowed or domain.endswith('.' + allowed) for allowed in ALLOWED_DOMAINS)
    except:
        return False

def cleanup_old_sessions():
    """Limpiar sesiones antiguas para evitar memory leaks"""
    global session_jobs
    cutoff = datetime.now() - timedelta(hours=SESSION_TIMEOUT_HOURS)
    sessions_to_remove = []
    
    for session_id, jobs in session_jobs.items():
        # Verificar si todos los jobs son antiguos
        all_old = True
        for job_id, job in jobs.items():
            created = datetime.fromisoformat(job.get('created_at', datetime.now().isoformat()))
            if created > cutoff:
                all_old = False
                break
        if all_old and jobs:
            sessions_to_remove.append(session_id)
    
    for session_id in sessions_to_remove:
        del session_jobs[session_id]
        logger.info(f"Session {session_id} limpiada por antigüedad")
    
    # Limitar número total de sesiones
    if len(session_jobs) > MAX_SESSIONS:
        oldest = sorted(session_jobs.keys())[:len(session_jobs) - MAX_SESSIONS]
        for session_id in oldest:
            del session_jobs[session_id]

# Estado por sesión - cada usuario ve solo sus descargas
session_jobs = {}  # {session_id: {job_id: job_data}}
download_threads = {}

class SessionDownloadManager:
    """Manejador de descargas por sesión de usuario"""
    
    def get_session_jobs(self, session_id):
        """Obtener trabajos de una sesión específica"""
        if session_id not in session_jobs:
            session_jobs[session_id] = {}
        return session_jobs[session_id]
    
    def start_download(self, session_id, url, quality='best', format_id=None):
        """Iniciar obtención de enlace de descarga directa"""
        job_id = str(uuid.uuid4())
        
        # Asegurar que existe la sesión
        if session_id not in session_jobs:
            session_jobs[session_id] = {}
        
        # Estado inicial del trabajo
        session_jobs[session_id][job_id] = {
            'id': job_id,
            'session_id': session_id,
            'url': url,
            'status': 'processing',
            'progress': 0,
            'created_at': datetime.now().isoformat(),
            'completed_at': None,
            'error': None,
            'download_url': None,
            'filename': None,
            'file_size': 0,
            'title': 'Obteniendo información...',
            'duration': 0,
            'quality_requested': quality
        }
        
        # Iniciar hilo para obtener enlace de descarga
        thread = threading.Thread(
            target=self._process_download_link,
            args=(session_id, job_id, url, quality, format_id),
            daemon=True
        )
        download_threads[f"{session_id}_{job_id}"] = thread
        thread.start()
        
        return job_id
    
    def _process_download_link(self, session_id, job_id, url, quality, format_id):
        """Procesar y obtener enlace de descarga directa"""
        try:
            logger.info(f"Session {session_id}, Job {job_id}: Obteniendo enlace de descarga...")
            
            # Configuración de yt-dlp para obtener URLs directas
            # Soporte multi-plataforma: YouTube, Instagram, TikTok, Facebook, etc.
            
            # Ruta de cookies para YouTube (evita bloqueo anti-bot)
            cookies_path = '/app/cookies/youtube.txt'
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'format': self._get_format_selector(quality, format_id),
                'fragment_retries': 50,    # Reintentos para fragmentos
                'retries': 20,             # Reintentos generales
                'file_access_retries': 10, # Reintentos de acceso a archivo
                'cookiefile': cookies_path if os.path.exists(cookies_path) else None,
                
                # Configuración para resolver YouTube n-challenge
                'js_runtimes': {'node': {'exe': '/usr/bin/node'}},
                'remote_components': {'ejs:github'},
                
                # Headers anti-detección mejorados
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'referer': url,  # Usar la URL como referer
                'headers': {
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
                
                # Configuración específica por plataforma
                'extractor_args': {
                    'instagram': {
                        'comment_count': 0,  # No extraer comentarios
                    },
                    'youtube': {
                        'skip': [],  # No skipear formatos
                        'player_client': ['android', 'web', 'ios']
                    },
                    'tiktok': {
                        'api_hostname': 'api.tiktokv.com',
                    }
                }
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extraer información y URLs
                info = ydl.extract_info(url, download=False)
                
                # Obtener la mejor URL de descarga
                download_url = info.get('url')
                if not download_url:
                    # Buscar en formatos disponibles
                    formats = info.get('formats', [])
                    if formats:
                        # Tomar el primer formato válido
                        for fmt in formats:
                            if fmt.get('url'):
                                download_url = fmt['url']
                                break
                
                if not download_url:
                    raise Exception("No se pudo obtener URL de descarga")
                
                # Generar nombre de archivo seguro
                title = info.get('title', 'video')
                ext = info.get('ext', 'mp4')
                filename = self._sanitize_filename(f"{title}.{ext}")
                
                # Actualizar estado del trabajo
                session_jobs[session_id][job_id].update({
                    'status': 'ready',
                    'progress': 100,
                    'download_url': download_url,
                    'filename': filename,
                    'file_size': info.get('filesize') or info.get('filesize_approx', 0),
                    'title': title,
                    'duration': info.get('duration', 0),
                    'completed_at': datetime.now().isoformat()
                })
                
                logger.info(f"Session {session_id}, Job {job_id}: ✓ Enlace de descarga listo")
                
        except Exception as e:
            error_msg = str(e)
            session_jobs[session_id][job_id].update({
                'status': 'error',
                'error': error_msg,
                'completed_at': datetime.now().isoformat()
            })
            logger.error(f"Session {session_id}, Job {job_id}: ✗ Error - {error_msg}")
    
    def _get_format_selector(self, quality, format_id):
        """Obtener selector de formato según calidad o format_id."""
        if format_id:
            return format_id
        
        if quality == 'audio':
            return 'bestaudio/best'
        if quality == 'best':
            return 'best'
        
        # Si no es una palabra clave, 'quality' es el format_id.
        return quality
    
    def _sanitize_filename(self, filename):
        """Limpiar nombre de archivo para ser seguro"""
        # Remover caracteres no válidos
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # Limitar longitud
        if len(filename) > 100:
            name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            filename = name[:90] + ('.' + ext if ext else '')
        return filename
    
    def get_job_status(self, session_id, job_id):
        """Obtener estado de un trabajo específico de una sesión"""
        session_data = self.get_session_jobs(session_id)
        return session_data.get(job_id, None)
    
    def cancel_job(self, session_id, job_id):
        """Cancelar un trabajo de una sesión"""
        session_data = self.get_session_jobs(session_id)
        if job_id in session_data:
            session_data[job_id]['status'] = 'cancelled'
            session_data[job_id]['completed_at'] = datetime.now().isoformat()
            return True
        return False
    
    def list_session_jobs(self, session_id):
        """Listar trabajos de una sesión específica"""
        session_data = self.get_session_jobs(session_id)
        return list(session_data.values())

# Instancia global del manejador
download_manager = SessionDownloadManager()

def get_session_id():
    """Obtener session_id del request"""
    return request.headers.get('X-Session-ID', 'default')

@app.route('/')
def serve_frontend():
    """Servir interfaz estática del frontend"""
    return app.send_static_file('index.html')

@app.route('/api/info')
def api_info():
    """Información general de la API"""
    return jsonify({
        'message': 'Sistema de Descargas por Sesión - Video Downloader',
        'version': '3.0',
        'features': [
            'Descargas por sesión de usuario',
            'Descarga directa al dispositivo',
            'Sin almacenamiento en servidor',
            'Soporte multi-plataforma',
            'Privacidad total por sesión'
        ],
        'endpoints': [
            'POST /formats - Obtener formatos disponibles',
            'POST /start - Iniciar descarga',
            'GET /status/<job_id> - Ver estado',
            'GET /download/<job_id> - Descargar archivo',
            'DELETE /cancel/<job_id> - Cancelar',
            'GET /jobs - Listar trabajos de sesión'
        ]
    })

@app.route('/start', methods=['POST', 'OPTIONS'])
def start_download():
    """Iniciar procesamiento de descarga"""
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, X-Session-ID')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    
    try:
        # Limpiar sesiones antiguas periódicamente
        cleanup_old_sessions()
        
        session_id = get_session_id()
        data = request.get_json()
        
        if not data or 'url' not in data:
            return jsonify({'error': 'URL requerida'}), 400
        
        url = data['url']
        
        # Validación de seguridad de URL
        if not validate_url(url):
            return jsonify({'error': 'URL no permitida. Solo se aceptan: YouTube, Instagram, TikTok, Facebook, Twitter, Vimeo'}), 400
        
        quality = data.get('quality', 'best')
        format_id = data.get('format_id', None)
        
        job_id = download_manager.start_download(session_id, url, quality, format_id)
        
        response = jsonify({
            'success': True,
            'job_id': job_id,
            'session_id': session_id,
            'message': 'Procesando enlace de descarga...',
            'status_url': f'/status/{job_id}'
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
        
    except Exception as e:
        response = jsonify({'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500

@app.route('/status/<job_id>')
def get_status(job_id):
    """Obtener estado de descarga de la sesión actual"""
    session_id = get_session_id()
    job = download_manager.get_job_status(session_id, job_id)
    
    if not job:
        logger.warning(f"Job {job_id} not found for Session {session_id}. Headers: {dict(request.headers)}")
        # Debug: check if job exists in any session
        found_in = None
        for s_id, jobs in session_jobs.items():
            if job_id in jobs:
                found_in = s_id
                break
        if found_in:
             logger.warning(f"Job FOUND in DIFFERENT session: {found_in}")

    
    if not job:
        response = jsonify({'error': 'Trabajo no encontrado en esta sesión'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 404
    
    response = jsonify(job)
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/download/<job_id>')
def download_file(job_id):
    """Redirigir a descarga directa"""
    session_id = get_session_id()
    job = download_manager.get_job_status(session_id, job_id)
    
    if not job:
        return jsonify({'error': 'Trabajo no encontrado'}), 404
    
    if job['status'] != 'ready':
        return jsonify({'error': 'Descarga no está lista'}), 400
    
    download_url = job.get('download_url')
    filename = job.get('filename', 'video.mp4')
    
    if not download_url:
        return jsonify({'error': 'URL de descarga no disponible'}), 500
    
    # Retornar la URL para descarga directa
    response = jsonify({
        'download_url': download_url,
        'filename': filename,
        'file_size': job.get('file_size', 0)
    })
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/cancel/<job_id>', methods=['DELETE', 'OPTIONS'])
def cancel_download(job_id):
    """Cancelar descarga de la sesión actual"""
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'DELETE')
        return response
    
    session_id = get_session_id()
    
    if download_manager.cancel_job(session_id, job_id):
        response = jsonify({'success': True, 'message': 'Descarga cancelada'})
    else:
        response = jsonify({'error': 'Trabajo no encontrado en esta sesión'})
    
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/jobs')
def list_jobs():
    """Listar trabajos de la sesión actual únicamente"""
    session_id = get_session_id()
    jobs = download_manager.list_session_jobs(session_id)
    
    response = jsonify({
        'session_id': session_id,
        'jobs': jobs,
        'total': len(jobs),
        'active': len([j for j in jobs if j['status'] in ['processing']])
    })
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/formats', methods=['POST', 'OPTIONS'])
def get_video_formats():
    """Obtener formatos disponibles para un video sin descargarlo"""
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, X-Session-ID')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    
    try:
        data = request.get_json()
        
        if not data or 'url' not in data:
            response = jsonify({'error': 'URL requerida', 'success': False})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        url = data['url']
        
        # Validación de seguridad de URL
        if not validate_url(url):
            response = jsonify({'error': 'URL no permitida. Solo se aceptan: YouTube, Instagram, TikTok, Facebook, Twitter, Vimeo', 'success': False})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        logger.info(f"Obteniendo formatos para URL válida")
        
        # Configuración básica de yt-dlp - SIN FILTROS NI LIMITACIONES
        # Soporte para múltiples plataformas: YouTube, Instagram, TikTok, etc.
        
        # Ruta de cookies para YouTube (evita bloqueo anti-bot)
        cookies_path = '/app/cookies/youtube.txt'
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'cookiefile': cookies_path if os.path.exists(cookies_path) else None,
            
            # Configuración para resolver YouTube n-challenge
            'js_runtimes': {'node': {'exe': '/usr/bin/node'}},
            'remote_components': {'ejs:github'},
            
            # Headers generales para compatibilidad con múltiples sitios
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': url,  # Usar la URL como referer para mejor compatibilidad
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            # Configuraciones para Instagram
            'extractor_args': {
                'instagram': {
                    'comment_count': 0,  # No extraer comentarios para mayor velocidad
                },
                'youtube': {
                    'skip': [],  # No skipear nada para obtener todos los formatos
                    'player_client': ['android', 'web', 'ios']
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extraer información del video
            info = ydl.extract_info(url, download=False)
            
            if not info:
                raise Exception("No se pudo obtener información del video")
            
            # Verificar si es un livestream que terminó
            if info.get('live_status') == 'was_live':
                raise Exception("Este evento en vivo ya terminó y no está disponible para descarga")
            
            # Verificar si es un livestream activo
            if info.get('live_status') == 'is_live':
                raise Exception("No se pueden descargar transmisiones en vivo activas")
            
            # Obtener información básica
            video_info = {
                'title': info.get('title', 'Video sin título'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail'),
                'uploader': info.get('uploader', 'Desconocido'),
                'view_count': info.get('view_count', 0),
                'upload_date': info.get('upload_date', ''),
                'description': (info.get('description', '') or '')[:500] + '...' if info.get('description') else ''
            }
            
            # Procesar formatos disponibles
            formats = info.get('formats', [])
            logger.info(f"🔍 DIAGNÓSTICO COMPLETO DE FORMATOS PARA: {video_info['title']}")
            logger.info(f"📊 TOTAL DETECTADO POR YT-DLP: {len(formats)} formatos")
            logger.info("="*80)
            
            # Contadores de diagnóstico
            formatos_con_url = 0
            formatos_sin_url = 0
            formatos_video_audio = 0
            formatos_solo_video = 0
            formatos_solo_audio = 0
            resoluciones_unicas = set()
            
            # LOG ULTRA-DETALLADO DE CADA FORMATO
            for i, fmt in enumerate(formats):
                format_id = fmt.get('format_id', 'SIN_ID')
                height = fmt.get('height')
                width = fmt.get('width')
                fps = fmt.get('fps')
                vcodec = fmt.get('vcodec', 'none')
                acodec = fmt.get('acodec', 'none')
                ext = fmt.get('ext', 'unknown')
                url_presente = bool(fmt.get('url'))
                
                logger.info(f"📋 FORMATO #{i+1:3d} - ID: {format_id}")
                logger.info(f"    ➤ Resolución: {width}x{height} ({height}p)" if height else "    ➤ Resolución: No especificada")
                logger.info(f"    ➤ FPS: {fps}" if fps else "    ➤ FPS: No especificado")
                logger.info(f"    ➤ Extensión: {ext}")
                logger.info(f"    ➤ Video codec: {vcodec}")
                logger.info(f"    ➤ Audio codec: {acodec}")
                logger.info(f"    ➤ URL presente: {'✅ SÍ' if url_presente else '❌ NO'}")
                
                if url_presente:
                    formatos_con_url += 1
                    if height:
                        resoluciones_unicas.add(height)
                else:
                    formatos_sin_url += 1
                    
                if vcodec != 'none' and acodec != 'none':
                    formatos_video_audio += 1
                    logger.info(f"    ➤ Tipo: 🎬 VIDEO + AUDIO")
                elif vcodec != 'none':
                    formatos_solo_video += 1
                    logger.info(f"    ➤ Tipo: 📹 SOLO VIDEO")
                elif acodec != 'none':
                    formatos_solo_audio += 1
                    logger.info(f"    ➤ Tipo: 🎵 SOLO AUDIO")
                else:
                    logger.info(f"    ➤ Tipo: ❓ DESCONOCIDO")
                
                logger.info("-" * 60)
            
            # RESUMEN DE DIAGNÓSTICO
            logger.info("📈 RESUMEN DEL DIAGNÓSTICO:")
            logger.info(f"    ✅ Formatos con URL válida: {formatos_con_url}")
            logger.info(f"    ❌ Formatos sin URL: {formatos_sin_url}")
            logger.info(f"    🎬 Video+Audio: {formatos_video_audio}")
            logger.info(f"    📹 Solo Video: {formatos_solo_video}")
            logger.info(f"    🎵 Solo Audio: {formatos_solo_audio}")
            logger.info(f"    📏 Resoluciones únicas detectadas: {sorted(resoluciones_unicas, reverse=True)}")
            logger.info("="*80)
            
            available_formats = []
            video_formats = []
            audio_formats = []

            # Procesar TODOS los formatos válidos (con URL)
            for i, fmt in enumerate(formats):
                if not fmt.get('url'):
                    logger.info(f"FORMATO {i+1} DESCARTADO: No tiene URL")
                    continue
                    
                format_info = {
                    'format_id': fmt.get('format_id'),
                    'ext': fmt.get('ext', 'unknown'),
                    'filesize': fmt.get('filesize') or fmt.get('filesize_approx'),
                    'tbr': fmt.get('tbr'),
                    'abr': fmt.get('abr'),
                    'vbr': fmt.get('vbr'),
                    'fps': fmt.get('fps'),
                    'height': fmt.get('height'),
                    'width': fmt.get('width'),
                    'resolution': fmt.get('resolution', f"{fmt.get('width', '?')}x{fmt.get('height', '?')}")
                }
                
                # Clasificar TODOS los tipos de formato
                if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                    # Video + Audio combinado
                    format_info.update({
                        'type': 'video+audio',
                        'vcodec': fmt.get('vcodec', 'unknown'),
                        'acodec': fmt.get('acodec', 'unknown')
                    })
                    video_formats.append(format_info)
                elif fmt.get('vcodec') != 'none':
                    # Solo video
                    format_info.update({
                        'type': 'video',
                        'vcodec': fmt.get('vcodec', 'unknown')
                    })
                    video_formats.append(format_info)
                elif fmt.get('acodec') != 'none':
                    # Solo audio
                    format_info.update({
                        'type': 'audio',
                        'acodec': fmt.get('acodec', 'unknown'),
                        'sample_rate': fmt.get('asr')
                    })
                    audio_formats.append(format_info)
                else:
                    # Otros formatos (storyboards, etc.)
                    logger.info(f"Formato especial descartado: {fmt.get('format_id')} - {fmt.get('ext')}")
                    continue

            logger.info(f"📊 RESULTADO FINAL DEL PROCESAMIENTO:")
            logger.info(f"    🎬 Formatos Video+Audio: {len([f for f in video_formats if f['type'] == 'video+audio'])}")
            logger.info(f"    📹 Formatos Solo Video: {len([f for f in video_formats if f['type'] == 'video'])}")
            logger.info(f"    🎵 Formatos Solo Audio: {len(audio_formats)}")
            logger.info(f"    📝 Total para el combo: {len(video_formats) + len(audio_formats)}")
            logger.info("="*80)

            # === CONSTRUCCIÓN INTELIGENTE DE LISTA DE CALIDADES ===
            common_qualities = [{
                'value': 'best',
                'label': '⭐ Mejor Calidad (Automático)',
                'type': 'auto'
            }]
            
            logger.info(f"🔨 CONSTRUYENDO LISTA COMPLETA DE CALIDADES...")
            
            # Obtener todas las resoluciones únicas disponibles
            resoluciones_disponibles = set()
            for fmt in video_formats:
                height = fmt.get('height')
                if height and height > 0:
                    resoluciones_disponibles.add(height)
            
            # Ordenar resoluciones de mayor a menor
            resoluciones_ordenadas = sorted(resoluciones_disponibles, reverse=True)
            logger.info(f"📏 Resoluciones detectadas: {resoluciones_ordenadas}")
            
            calidades_agregadas = 0
            
            # Para cada resolución, encontrar los mejores formatos disponibles
            for resolucion in resoluciones_ordenadas:
                # Buscar formatos de esta resolución
                formatos_resolucion = [f for f in video_formats if f.get('height') == resolucion]
                
                # Prioritizar: Video+Audio > Solo Video (mayor calidad)
                formato_combinado = None
                formato_solo_video = None
                
                for fmt in formatos_resolucion:
                    if fmt['type'] == 'video+audio':
                        formato_combinado = fmt
                        break
                
                if not formato_combinado:
                    # Buscar el mejor formato solo video (mayor bitrate/calidad)
                    formatos_solo_video = [f for f in formatos_resolucion if f['type'] == 'video']
                    if formatos_solo_video:
                        # Ordenar por calidad (tbr o vbr)
                        formato_solo_video = max(formatos_solo_video, 
                                               key=lambda x: (x.get('vbr') or x.get('tbr') or 0))
                
                # Agregar el mejor formato encontrado para esta resolución
                formato_a_agregar = formato_combinado or formato_solo_video
                
                if formato_a_agregar:
                    height = formato_a_agregar.get('height') or 0
                    fps = formato_a_agregar.get('fps') or 0
                    format_id = formato_a_agregar.get('format_id', '')
                    
                    # Asegurar que height sea válido antes de continuar
                    if height <= 0:
                        continue
                    
                    # Construir etiqueta descriptiva
                    label = f"{height}p"
                    fps_value = fps or 0  # Convertir None a 0 para evitar errores de comparación
                    if fps_value > 25:
                        label += f" {fps_value}fps"
                    
                    if formato_a_agregar['type'] == 'video+audio':
                        label = f"✅ {label} Video+Audio"
                        icono = "🎬"
                    else:
                        label = f"📹 {label} Solo Video"
                        icono = "📹"
                        
                    quality_item = {
                        'value': format_id,
                        'label': label,
                        'type': formato_a_agregar['type']
                    }
                    
                    common_qualities.append(quality_item)
                    calidades_agregadas += 1
                    logger.info(f"    ✅ {icono} AGREGADO: {label} (ID: {format_id})")
            
            # Agregar formatos de audio
            audios_agregados = 0
            for fmt in audio_formats:
                try:
                    ext = fmt.get('ext', 'm4a')
                    abr = fmt.get('abr', 0)
                    format_id = fmt.get('format_id', '')
                    
                    label = f"🎵 Audio {ext.upper()}"
                    if abr > 0:
                        label += f" ({abr}k)"
                        
                    common_qualities.append({
                        'value': format_id,
                        'label': label,
                        'type': 'audio'
                    })
                    audios_agregados += 1
                    logger.info(f"    🎵 Audio agregado: {label}")
                except Exception as e:
                    logger.warning(f"Error procesando audio: {e}")
                    continue
            
            logger.info(f"� RESUMEN DEL COMBO GENERADO:")
            logger.info(f"    📺 Calidades de video: {calidades_agregadas}")
            logger.info(f"    🎵 Opciones de audio: {audios_agregados}")
            logger.info(f"    🏁 Total en combo: {len(common_qualities)} opciones")
            logger.info("="*80)
            # No limitar ni filtrar los formatos en la respuesta detallada
            response_data = {
                'success': True,
                'video_info': video_info,
                'common_qualities': common_qualities,
                'detailed_formats': {
                    'video_audio': [f for f in video_formats if f['type'] == 'video+audio'],
                    'video_only': [f for f in video_formats if f['type'] == 'video'],
                    'audio_only': audio_formats
                },
                'total_formats': len(formats)
            }
            
            response = jsonify(response_data)
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response
            
    except Exception as e:
        # Manejar errores específicos de diferentes plataformas
        error_msg = str(e)
        
        # Errores específicos de Instagram
        if 'instagram.com' in url.lower():
            if 'login' in error_msg.lower() or 'private' in error_msg.lower():
                error_msg = "Este contenido de Instagram es privado o requiere login"
            elif 'not found' in error_msg.lower():
                error_msg = "El contenido de Instagram no fue encontrado o fue eliminado"
            elif 'age' in error_msg.lower():
                error_msg = "Este contenido de Instagram tiene restricciones de edad"
        
        # Errores específicos de TikTok
        elif 'tiktok.com' in url.lower():
            if 'private' in error_msg.lower():
                error_msg = "Este video de TikTok es privado"
            elif 'region' in error_msg.lower():
                error_msg = "Este contenido de TikTok no está disponible en tu región"
        
        # Error genérico mejorado
        if len(error_msg) > 200:
            error_msg = "Error al procesar el video. Verifica que la URL sea válida y el contenido esté disponible."
        
        error_response = jsonify({
            'error': error_msg,
            'success': False,
            'platform_detected': 'instagram' if 'instagram.com' in url.lower() else 
                                'tiktok' if 'tiktok.com' in url.lower() else
                                'youtube' if 'youtube.com' in url.lower() or 'youtu.be' in url.lower() else
                                'unknown'
        })
        error_response.headers.add('Access-control-allow-origin', '*')
        return error_response, 400

if __name__ == '__main__':
    print("🚀 Iniciando Sistema de Descargas por Sesión...")
    print("✓ Cada usuario ve solo sus descargas")
    print("✓ Descarga directa al dispositivo")
    print("✓ Sin almacenamiento en servidor")
    print("✓ Soporte multi-plataforma")
    print("🔒 Seguridad: CORS restringido, validación de URLs")
    print("📍 Servidor: http://localhost:1005")
    
    # PRODUCCIÓN: debug=False para seguridad
    app.run(host='0.0.0.0', port=1005, debug=False, threaded=True)
