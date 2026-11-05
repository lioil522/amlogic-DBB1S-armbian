/* SPDX-License-Identifier: MIT
 * Headless Panfrost shader/readback check; does not take over the display.
 * cc -O2 -Wall -Wextra -Werror gpu-smoke.c -o gpu-smoke -lEGL -lGLESv2
 */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fail(const char *message)
{
	fprintf(stderr, "%s (EGL=0x%x, GL=0x%x)\n", message,
		eglGetError(), glGetError());
	exit(1);
}

static GLuint shader(GLenum type, const char *source)
{
	GLuint id = glCreateShader(type);
	GLint ok;
	GLchar log[2048];
	glShaderSource(id, 1, &source, NULL);
	glCompileShader(id);
	glGetShaderiv(id, GL_COMPILE_STATUS, &ok);
	if (!ok) {
		glGetShaderInfoLog(id, sizeof(log), NULL, log);
		fprintf(stderr, "%s\n", log);
		fail("Shader compilation failed");
	}
	return id;
}

int main(void)
{
	PFNEGLGETPLATFORMDISPLAYEXTPROC platform_display =
		(PFNEGLGETPLATFORMDISPLAYEXTPROC)
		eglGetProcAddress("eglGetPlatformDisplayEXT");
	if (!platform_display)
		fail("EGL_EXT_platform_base unavailable");
	EGLDisplay display = platform_display(EGL_PLATFORM_SURFACELESS_MESA,
					     EGL_DEFAULT_DISPLAY, NULL);
	EGLint major, minor, count;
	if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor))
		fail("Surfaceless EGL initialization failed");
	if (!eglBindAPI(EGL_OPENGL_ES_API))
		fail("EGL API selection failed");
	const EGLint attributes[] = {
		EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
		EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
		EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
		EGL_ALPHA_SIZE, 8, EGL_NONE
	};
	EGLConfig config;
	if (!eglChooseConfig(display, attributes, &config, 1, &count) || count != 1)
		fail("No RGBA8 ES2 pbuffer configuration");
	const EGLint surface_attributes[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
	const EGLint context_attributes[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
	EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
	EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
	if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT ||
	    !eglMakeCurrent(display, surface, surface, context))
		fail("EGL context creation failed");
	const char *renderer = (const char *)glGetString(GL_RENDERER);
	printf("EGL %d.%d: %s\nGL renderer: %s\nGL version: %s\n", major, minor,
	       eglQueryString(display, EGL_VENDOR), renderer ? renderer : "unknown",
	       glGetString(GL_VERSION));
	if (!renderer || (!strstr(renderer, "Mali") && !strstr(renderer, "Panfrost") &&
			  !strstr(renderer, "panfrost"))) {
		fprintf(stderr, "FAIL: renderer is not Mali/Panfrost hardware\n");
		return 2;
	}
	GLuint vertex = shader(GL_VERTEX_SHADER,
		"attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }");
	GLuint fragment = shader(GL_FRAGMENT_SHADER,
		"precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }");
	GLuint program = glCreateProgram();
	glAttachShader(program, vertex);
	glAttachShader(program, fragment);
	glBindAttribLocation(program, 0, "pos");
	glLinkProgram(program);
	GLint linked;
	glGetProgramiv(program, GL_LINK_STATUS, &linked);
	if (!linked)
		fail("Shader program link failed");
	glUseProgram(program);
	const GLfloat vertices[] = { -1, -1, 3, -1, -1, 3 };
	glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, vertices);
	glEnableVertexAttribArray(0);
	glViewport(0, 0, 64, 64);
	glDisable(GL_DITHER);
	GLint color = glGetUniformLocation(program, "color");
	unsigned char pixels[64 * 64 * 4];
	for (int frame = 0; frame < 16; ++frame) {
		const int expected[] = { frame & 1 ? 191 : 64, 128, frame & 1 ? 64 : 191, 255 };
		glClearColor(0, 0, 0, 0);
		glClear(GL_COLOR_BUFFER_BIT);
		glUniform4f(color, expected[0] / 255.0f, expected[1] / 255.0f,
			    expected[2] / 255.0f, 1.0f);
		glDrawArrays(GL_TRIANGLES, 0, 3);
		glReadPixels(0, 0, 64, 64, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
		if (glGetError() != GL_NO_ERROR)
			fail("Rendering/readback failed");
		for (size_t i = 0; i < sizeof(pixels); ++i) {
			if (abs((int)pixels[i] - expected[i % 4]) > 2) {
				fprintf(stderr, "FAIL: frame=%d byte=%zu got=%u expected=%d\n",
					frame, i, pixels[i], expected[i % 4]);
				return 1;
			}
		}
	}
	glDeleteProgram(program);
	glDeleteShader(vertex);
	glDeleteShader(fragment);
	eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
	eglDestroyContext(display, context);
	eglDestroySurface(display, surface);
	eglTerminate(display);
	puts("PASS: 16 shader draws, 65536 pixels checked on Mali/Panfrost");
	return 0;
}
