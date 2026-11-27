package dev.blyadota.jobpilot.assistant;

import java.nio.ByteBuffer;

final class CapturePixels {
    private CapturePixels() {}
    static boolean hasContent(ByteBuffer rgba, int width, int height, int pitch) {
        // Ignore the system bars and letterboxing. A dark browser page with light text still passes.
        int lightest = 0, darkest = 255;
        int dy = Math.max(1, height / 240), dx = Math.max(1, width / 160);
        for (int y = height / 12; y < height - height / 12; y += dy) {
            for (int x = width / 24; x < width - width / 24; x += dx) {
                int row = Math.min(height - 1, y + ((x - width / 24) / dx) % dy);
                int i = row * pitch + x * 4;
                if ((rgba.get(i + 3) & 255) < 16) continue;
                int light = Math.max(rgba.get(i) & 255, Math.max(rgba.get(i+1) & 255, rgba.get(i+2) & 255));
                darkest = Math.min(darkest, light);
                lightest = Math.max(lightest, light);
                if (lightest - darkest >= 16) return true;
            }
        }
        return false;
    }
}
