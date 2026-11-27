package dev.blyadota.jobpilot.assistant;

import java.nio.ByteBuffer;

/** Run with javac and java -ea. No Android device or personal screenshot required. */
public final class CapturePixelsTest {
    public static void main(String[] args) {
        int width=320, height=640, pitch=width*4+32;
        ByteBuffer frame=ByteBuffer.allocate(height*pitch);
        assert !CapturePixels.hasContent(frame,width,height,pitch) : "Transparent startup frame";
        for (int y=0;y<height;y++) {
            for (int x=0;x<width;x++) frame.put(y*pitch+x*4+3,(byte)255);
            for (int i=width*4;i<pitch;i++) frame.put(y*pitch+i,(byte)255);
        }
        assert !CapturePixels.hasContent(frame,width,height,pitch) : "Black protected window with row padding";
        for (int y=0;y<24;y++)
            for (int x=0;x<width;x++) frame.put(y*pitch+x*4,(byte)255);
        assert !CapturePixels.hasContent(frame,width,height,pitch) : "System bars alone";
        for (int x=80;x<150;x++) frame.put(300*pitch+x*4,(byte)175);
        assert CapturePixels.hasContent(frame,width,height,pitch) : "Text on dark browser page";
        System.out.println("Capture pixel checks passed");
    }
}
