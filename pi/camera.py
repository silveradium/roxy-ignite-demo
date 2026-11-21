"""STAGE 3, not written yet: colour frames from the Orbbec Gemini 335 on the Pi's USB.

Deliberately the Orbbec and not the Go2's own camera. The Go2 sends H.264 over WebRTC and the
Pi 5 has no hardware H.264 decoder (HEVC only), so aiortc/PyAV would decode in software while
MediaPipe also wants CPU. The Orbbec is MJPEG over UVC, much cheaper, and it is the camera that
actually sees the person pointing -- which is what stage 4 needs.

Plan: open it with cv2.VideoCapture on its V4L2 index, MJPEG fourcc, keep only the newest frame
in a one-slot buffer (a grabber thread, so a slow consumer drops frames instead of lagging), and
hand frames to gestures.py and to the server's MJPEG endpoint (aiohttp StreamResponse,
multipart/x-mixed-replace). Colour over plain UVC should need no Orbbec SDK; only depth would.

Read ../Diectic-VLA-FYP/scripts/orbbec_view.py first: it already serves an MJPEG preview of this
camera with its field of view drawn on.
"""
