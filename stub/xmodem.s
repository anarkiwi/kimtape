; XMODEM-CRC receiver for the KIM-1 TTY, loaded and started by kimtape.
;
; Bit-bangs the TTY lines directly at CYC cycles per bit (define on the
; ca65 command line).  Input is 6530 port A bit 7, output port B bit 0;
; both idle high and a start bit is low, as the monitor's GETCH and OUTCH
; use them.  The bit rate the monitor measured at reset, in CNTL30/CNTH30,
; is left alone.  The zero page bytes it works in are saved on entry and
; restored on exit; a payload byte meant for one of them is sent to its
; place in the save area instead, and lands when they are restored.
;
; The XMODEM payload is a run of segments: address lo, hi, length lo, hi,
; then that many bytes.  A segment of length zero ends the run; its address
; is where execution continues after EOT, $FFFF meaning the monitor.  Every
; byte is read back after it is stored; one that does not stick sends CAN
; and the failing address, low byte first, and returns to the monitor.

.ifndef CYC
CYC = 52
.endif
.ifndef DATA
DATA = $0100            ; zero page save area, then the frame buffer
.endif

SAD     = $1740         ; 6530 port A: TTY in on bit 7
SBD     = $1742         ; 6530 port B: TTY out on bit 0
START   = $1C4F         ; monitor entry that keeps the measured bit rate

SOH     = $01
EOT     = $04
ACK     = $06
NAK     = $15
CAN     = $18
FRAME   = 133           ; SOH, block number and complement, 128 data, CRC
TRIES   = 6             ; silent waits before giving up
LONG    = 0             ; 256 * 256 * 11 cycles between retries
SHORT   = 9             ; idle time that ends a purge

ZP      = $F4           ; the monitor's register and scratch bytes
tmp     = ZP
sb0     = ZP + 1        ; port B with the TTY line low
blk     = ZP + 2        ; block number expected next
tries   = ZP + 3
hc      = ZP + 4        ; header bytes held: 0-3, 4 in data, 5 finished
ptr     = ZP + 5        ; segment address
len     = ZP + 7        ; segment length still to store
crc     = ZP + 9
NZP     = 11

; burn exactly n cycles; touches Y and N/Z only
.macro delay n
  .local loop
  .if ((n) < 0) .or ((n) = 1)
    .error "delay: impossible cycle count"
  .elseif (n) >= 11
    .if (((n) - 1) .mod 5) = 1
      ldy #((n) - 1) / 5 - 1
loop: dey
      bne loop
      delay 6
    .else
      ldy #((n) - 1) / 5
loop: dey
      bne loop
      delay (((n) - 1) .mod 5)
    .endif
  .elseif ((n) .mod 2) = 1
      bit $00
      delay (n) - 3
  .elseif (n) > 0
      nop
      delay (n) - 2
  .endif
.endmacro

.segment "CODE"

entry:  sei
        cld
        ldx #$FF
        txs
        ldx #NZP - 1
save:   lda ZP,x
        sta shadow,x
        dex
        bpl save
        inx
        stx hc
        inx
        stx blk
        lda SBD
        and #$FE
        sta sb0
        lda #'C'
reset:  ldy #TRIES
        sty tries
send:   pha
        lda #$80                ; sentinel bit for each received byte
        ldx #FRAME
fill:   sta buf-1,x
        dex
        bne fill
        pla
        jsr tx
        ldx #LONG

crit:
; Wait for a start bit, X * 256 polls 11 cycles apart, then take the rest
; of the frame from the stop bit of each byte, polls 7 cycles apart.  Both
; paths reach the first data sample 1.5 bits after the median edge they see.
poll:   bit SAD
        bpl first
        dey
        bne poll
        dex
        bne poll
timeout:
        dec tries
        beq exit
        lda #'C'                ; until the first block arrives
        ldy blk
        dey
        beq send
        lda #NAK
        bne send
first:  ldx #0
        delay CYC / 2 - 1
        jmp sample
wait:   bit SAD                 ; the stop bit
        bpl wait
edge:   bit SAD
        bmi edge
        delay CYC / 2 + 7
sample: delay CYC - 16
        lda SAD
        asl a
        ror buf,x
        bcc sample
        inx
        cpx #1
        beq start
        cpx #FRAME
        bne wait
        beq check
start:  lda buf
        cmp #SOH
        beq wait
        cmp #EOT
        beq eot
        cmp #CAN
        beq exit
purge:  ldx #SHORT              ; let the line go idle, then ask again
idle:   bit SAD
        bpl purge
        dey
        bne idle
        dex
        bne idle
        beq timeout

eot:    lda hc                  ; only once the run has ended
        cmp #5
        bne purge
        lda #ACK
        jsr tx
        lda ptr
        and ptr+1
        cmp #$FF
        beq exit
        lda ptr
        sta go+1
        lda ptr+1
        sta go+2
exit:   ldx #NZP - 1
restore:
        lda shadow,x
        sta ZP,x
        dex
        bpl restore
go:     jmp START

; send A as 8N1, two bits after the call: the line echoes what it receives,
; so a byte answered at once would start inside the stop bit of its echo
tx:     sta tmp
        delay CYC * 2
        ldx #10
        clc                     ; start bit
txbit:  lda sb0
        adc #0
        sta SBD
        delay CYC - 21
        sec                     ; stop bit
        ror tmp
        dex
        bne txbit
        rts
critend:

check:  lda buf+1
        eor buf+2
        cmp #$FF
        bne fail
        lda #0
        sta crc
        sta crc+1
        ldx #3
sum:    lda buf,x
        jsr update
        inx
        cpx #FRAME
        bne sum
        lda crc
        ora crc+1
        bne fail
        lda buf+1
        cmp blk
        beq new
        tay                     ; the previous block again: our ACK was lost
        iny
        cpy blk
        beq ack
fail:   jmp purge

new:    ldx #3
byte:   lda buf,x
        ldy hc
        cpy #4
        beq data
        bcs next                ; past the end of the run
        sta ptr,y
        inc hc
        cpy #3
        bne next
        lda len
        ora len+1
        bne next
        inc hc                  ; zero length ends the run
        bne next
data:   ldy #0
        sta (ptr),y
        cmp (ptr),y
        bne bad
        inc ptr
        bne count
        inc ptr+1
count:  lda len
        bne low
        dec len+1
low:    dec len
        bne next
        lda len+1
        bne next
        sta hc
next:   inx
        cpx #FRAME - 2
        bne byte
        inc blk
ack:    lda #ACK
        jmp reset

bad:    lda #CAN
        jsr tx
        lda ptr
        jsr tx
        lda ptr+1
        jsr tx
        jmp exit

; CRC-16/XMODEM of A into crc, after Greg Cook; preserves X
update: eor crc+1
        sta crc+1
        lsr a
        lsr a
        lsr a
        lsr a
        tay
        asl a
        eor crc
        sta crc
        tya
        eor crc+1
        sta crc+1
        asl a
        asl a
        asl a
        tay
        asl a
        asl a
        eor crc+1
        sta crc+1
        tya
        rol a
        eor crc
        ldy crc+1
        sta crc+1
        sty crc
        rts

image:

shadow  = DATA
buf     = shadow + NZP
DATASIZE = NZP + FRAME
.export ZP, NZP, DATASIZE
