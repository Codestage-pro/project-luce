package pro.codestage.luce

import org.junit.Assert.assertEquals
import org.junit.Test

class StereoOpusTest {
    // o libwebrtc decodifica em mono se a descrição LOCAL não tiver stereo=1: quem assiste ouvia L+R nos dois lados
    @Test
    fun addsStereoToOpusFmtpOnly() {
        val offer = "m=audio 9 UDP/TLS/RTP/SAVPF 111 63\r\n" +
            "a=rtpmap:111 opus/48000/2\r\n" +
            "a=fmtp:111 minptime=10;useinbandfec=1;stereo=0\r\n" +
            "a=rtpmap:63 red/48000/2\r\n" +
            "a=fmtp:63 111/111\r\n"
        val out = LuceApi.withStereoOpus(offer)
        assertEquals(
            offer.replace("a=fmtp:111 minptime=10;useinbandfec=1;stereo=0", "a=fmtp:111 minptime=10;useinbandfec=1;stereo=1"),
            out
        )
    }
}
