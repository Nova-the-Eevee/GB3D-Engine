; ============================================================
; GB3D Studio 1.2 - SM83 exact DDA raster core
; ============================================================
;
; This deliberately replaces 1.1's broken two-row experimental rasterizer.
; Output is the known-correct single-scanline packed-tile renderer, but edge
; walking now uses quotient/remainder DDA. Each edge step has a fixed amount
; of work and at most ONE remainder correction; there is no dx/dy inner loop.
;
; C prepares, for each edge:
;   q = sign(dx) * (abs(dx) / dy), stored as an 8-bit modular delta
;   r = abs(dx) % dy
; Then each scanline does:
;   x += q
;   err += r
;   if carry || err >= dy: err -= dy; x += sign
;
; The remainder accumulator is only one byte. Because it is always < dy
; before the add, the carry flag is exactly the missing 9th bit needed when
; err+r crosses 255. This removes the old 16-bit error bookkeeping.
;
; This is exactly equivalent to x = x0 + floor(n*dx/dy) used by the old
; Bresenham-style loop, so coverage stays deterministic while steep edges get
; much cheaper.
; ============================================================

.globl _gb3d_fill_bytes_asm
.globl _gb3d_clear_visible_asm
.globl _gb3d_draw_triangle_asm
.globl _gb3d_triangle_queue_begin_asm
.globl _gb3d_triangle_queue_flush_asm

.globl _triangle_queue
.globl _triangle_bucket_heads
.globl _triangle_count

.globl _gb3d_asm_ptr
.globl _gb3d_asm_count
.globl _gb3d_asm_value

; These are pointer variables to 16-byte aligned storage.
.globl _framebuffer_tilemap
.globl _framebuffer_attributes

.globl _gb3d_tri_x0
.globl _gb3d_tri_y0
.globl _gb3d_tri_x1
.globl _gb3d_tri_y1
.globl _gb3d_tri_x2
.globl _gb3d_tri_y2
.globl _gb3d_tri_material
.globl _gb3d_tri_palette
.globl _gb3d_tri_shade
.globl _gb3d_tri_pair_top
.globl _gb3d_tri_pair_bottom

.globl _gb3d_top_rows
.globl _gb3d_bottom_rows

.globl _gb3d_long_q
.globl _gb3d_long_r
.globl _gb3d_long_dy
.globl _gb3d_long_step
.globl _gb3d_top_q
.globl _gb3d_top_r
.globl _gb3d_top_dy
.globl _gb3d_top_step
.globl _gb3d_bottom_q
.globl _gb3d_bottom_r
.globl _gb3d_bottom_dy
.globl _gb3d_bottom_step

.globl _gb3d_current_y
.globl _gb3d_long_x
.globl _gb3d_short_x
.globl _gb3d_long_err
.globl _gb3d_short_err
.globl _gb3d_rows_left
.globl _gb3d_span_x1
.globl _gb3d_span_x2

.area _CODE

; ============================================================
; TRIANGLE QUEUE HOT PATH
; ============================================================
; GB3DQueuedTriangle is deliberately asserted to be 8 bytes in C:
;   x0 y0 x1 y1 x2 y2 material next
; Therefore queue[index] is base + index*8: three ADD HL,HL instructions.
; Keeping bucket traversal here avoids C pointer arithmetic, linked-list
; bookkeeping and seven C stores before every rasterizer call.

_gb3d_triangle_queue_begin_asm::
    xor a
    ld  (_triangle_count), a
    ld  hl, #_triangle_bucket_heads
    ld  b, #16
    ld  a, #0xFF
_gb3d_r_queue_clear_loop:
    ld  (hl+), a
    dec b
    jr  nz, _gb3d_r_queue_clear_loop
    ret

_gb3d_triangle_queue_flush_asm::
    ld  b, #16
_gb3d_r_queue_bucket:
    dec b

    ; A = head index for this far-to-near bucket.
    ld  hl, #_triangle_bucket_heads
    ld  e, b
    ld  d, #0
    add hl, de
    ld  a, (hl)

_gb3d_r_queue_record:
    cp  #0xFF
    jr  z, _gb3d_r_queue_next_bucket

    ; HL = triangle_queue + index*8.
    ld  l, a
    ld  h, #0
    add hl, hl
    add hl, hl
    add hl, hl
    ld  de, #_triangle_queue
    add hl, de

    ; Read the linked-list next byte at +7, then restore HL to the record.
    ; The draw-record entry consumes the first seven bytes directly, avoiding
    ; the old C -> globals -> ASM load/store round trip.
    ld  de, #0x0007
    add hl, de
    ld  a, (hl)
    push af
    ld  de, #0xFFF9
    add hl, de

    ; Triangle setup/rasterization clobbers all general registers. Preserve
    ; only the next index and current bucket counter across the call.
    push bc
    call _gb3d_draw_triangle_record
    pop bc
    pop af
    jr  _gb3d_r_queue_record

_gb3d_r_queue_next_bucket:
    ld  a, b
    or  a
    jr  nz, _gb3d_r_queue_bucket
    ret

; ------------------------------------------------------------
; void gb3d_fill_bytes_asm(void)
; ------------------------------------------------------------
_gb3d_fill_bytes_asm::
    ld  hl, #_gb3d_asm_ptr
    ld  a, (hl+)
    ld  e, a
    ld  a, (hl)
    ld  d, a

    ld  hl, #_gb3d_asm_count
    ld  a, (hl+)
    ld  c, a
    ld  a, (hl)
    ld  b, a

    ld  a, b
    or  c
    ret z

    ld  a, (_gb3d_asm_value)
    ld  h, a
_gb3d_r_gb3d_fill_loop:
    ld  a, h
    ld  (de), a
    inc de
    dec bc
    ld  a, b
    or  c
    jr  nz, _gb3d_r_gb3d_fill_loop
    ret

; ------------------------------------------------------------
; void gb3d_clear_visible_asm(void)
; ------------------------------------------------------------
; Native BG map stride is 32 bytes, but only 20 columns are visible.
_gb3d_clear_visible_asm::
    ld  hl, #_gb3d_asm_ptr
    ld  a, (hl+)
    ld  e, a
    ld  a, (hl)
    ld  d, a
    ld  a, (_gb3d_asm_value)
    ld  h, a
    ld  b, #18
_gb3d_r_gb3d_clear_row:
    ld  c, #20
_gb3d_r_gb3d_clear_col:
    ld  a, h
    ld  (de), a
    inc de
    dec c
    jr  nz, _gb3d_r_gb3d_clear_col
    ld  a, e
    add a, #12
    ld  e, a
    jr  nc, _gb3d_r_gb3d_clear_no_carry
    inc d
_gb3d_r_gb3d_clear_no_carry:
    dec b
    jr  nz, _gb3d_r_gb3d_clear_row
    ret

; ============================================================
; EDGE STEPPERS - quotient/remainder DDA
; ============================================================
;
; 1.2 raster-hot-path pass:
;   * q is already signed, so x += q has no per-row sign branch.
;   * errors are 8-bit; carry from err+r is the ninth bit.
;   * long+short edges are advanced by one combined call per row instead of
;     two separate CALL/RET pairs.
;   * dy is guaranteed non-zero whenever a stepper is actually called. The
;     bottom loop does not step after its final row, so flat-bottom triangles
;     never enter a zero-height edge step.
;

; Advance the long edge, then the top short edge.
_gb3d_step_edges_top:
    ; ---- long x += signed q ----
    ld  hl, #_gb3d_long_x
    ld  a, (_gb3d_long_q)
    add a, (hl)
    ld  (hl), a

    ; ---- long err += r; if carry || err >= dy, correct once ----
    ld  a, (_gb3d_long_err)
    ld  c, a
    ld  a, (_gb3d_long_r)
    add a, c
    ld  c, a
    ld  a, (_gb3d_long_dy)
    ld  b, a
    jr  c, _gb3d_r_long_top_correct
    ld  a, c
    cp  b
    jr  c, _gb3d_r_long_top_store
_gb3d_r_long_top_correct:
    ld  a, c
    sub b
    ld  (_gb3d_long_err), a
    ld  hl, #_gb3d_long_x
    ld  a, (_gb3d_long_step)
    add a, (hl)
    ld  (hl), a
    jr  _gb3d_r_short_top_begin
_gb3d_r_long_top_store:
    ld  a, c
    ld  (_gb3d_long_err), a

_gb3d_r_short_top_begin:
    ; ---- short/top x += signed q ----
    ld  hl, #_gb3d_short_x
    ld  a, (_gb3d_top_q)
    add a, (hl)
    ld  (hl), a

    ; ---- short/top remainder correction ----
    ld  a, (_gb3d_short_err)
    ld  c, a
    ld  a, (_gb3d_top_r)
    add a, c
    ld  c, a
    ld  a, (_gb3d_top_dy)
    ld  b, a
    jr  c, _gb3d_r_short_top_correct
    ld  a, c
    cp  b
    jr  c, _gb3d_r_short_top_store
_gb3d_r_short_top_correct:
    ld  a, c
    sub b
    ld  (_gb3d_short_err), a
    ld  hl, #_gb3d_short_x
    ld  a, (_gb3d_top_step)
    add a, (hl)
    ld  (hl), a
    ret
_gb3d_r_short_top_store:
    ld  a, c
    ld  (_gb3d_short_err), a
    ret

; Advance the long edge, then the bottom short edge.
; The long half is duplicated intentionally: saving one CALL/RET per scanline
; is worth a little ROM in the hottest routine in the engine.
_gb3d_step_edges_bottom:
    ; ---- long x += signed q ----
    ld  hl, #_gb3d_long_x
    ld  a, (_gb3d_long_q)
    add a, (hl)
    ld  (hl), a

    ; ---- long remainder correction ----
    ld  a, (_gb3d_long_err)
    ld  c, a
    ld  a, (_gb3d_long_r)
    add a, c
    ld  c, a
    ld  a, (_gb3d_long_dy)
    ld  b, a
    jr  c, _gb3d_r_long_bottom_correct
    ld  a, c
    cp  b
    jr  c, _gb3d_r_long_bottom_store
_gb3d_r_long_bottom_correct:
    ld  a, c
    sub b
    ld  (_gb3d_long_err), a
    ld  hl, #_gb3d_long_x
    ld  a, (_gb3d_long_step)
    add a, (hl)
    ld  (hl), a
    jr  _gb3d_r_short_bottom_begin
_gb3d_r_long_bottom_store:
    ld  a, c
    ld  (_gb3d_long_err), a

_gb3d_r_short_bottom_begin:
    ; ---- short/bottom x += signed q ----
    ld  hl, #_gb3d_short_x
    ld  a, (_gb3d_bottom_q)
    add a, (hl)
    ld  (hl), a

    ; ---- short/bottom remainder correction ----
    ld  a, (_gb3d_short_err)
    ld  c, a
    ld  a, (_gb3d_bottom_r)
    add a, c
    ld  c, a
    ld  a, (_gb3d_bottom_dy)
    ld  b, a
    jr  c, _gb3d_r_short_bottom_correct
    ld  a, c
    cp  b
    jr  c, _gb3d_r_short_bottom_store
_gb3d_r_short_bottom_correct:
    ld  a, c
    sub b
    ld  (_gb3d_short_err), a
    ld  hl, #_gb3d_short_x
    ld  a, (_gb3d_bottom_step)
    add a, (hl)
    ld  (hl), a
    ret
_gb3d_r_short_bottom_store:
    ld  a, c
    ld  (_gb3d_short_err), a
    ret

; ============================================================
; CLIP + PACKED SPAN WRITER
; ============================================================
; Clipping now falls straight into the writer instead of CALLing a clip helper
; and then tail-jumping into the writer. That removes one CALL/RET pair from
; every rasterized scanline while keeping exactly the same coverage rules.
;
; One 8x8 BG tile stores two logical pixels across and two down:
;   TL bits 0..1, TR 2..3, BL 4..5, BR 6..7.
; Native RAM map stride is 32 tiles.
_gb3d_draw_span:
    ld  a, (_gb3d_current_y)
    bit 7, a
    jp  nz, _gb3d_r_draw_span_ret
    cp  #36
    jp  nc, _gb3d_r_draw_span_ret

    ; Signed min/max. XOR $80 converts signed byte ordering to unsigned.
    ld  a, (_gb3d_long_x)
    ld  b, a
    xor #0x80
    ld  c, a
    ld  a, (_gb3d_short_x)
    ld  d, a
    xor #0x80
    cp  c
    jr  nc, _gb3d_r_span_long_min

    ld  a, d
    ld  (_gb3d_span_x1), a
    ld  a, b
    ld  (_gb3d_span_x2), a
    jr  _gb3d_r_span_have_minmax
_gb3d_r_span_long_min:
    ld  a, b
    ld  (_gb3d_span_x1), a
    ld  a, d
    ld  (_gb3d_span_x2), a
_gb3d_r_span_have_minmax:

    ld  a, (_gb3d_span_x2)
    bit 7, a
    jp  nz, _gb3d_r_draw_span_ret

    ld  a, (_gb3d_span_x1)
    bit 7, a
    jr  z, _gb3d_r_span_x1_nonneg
    xor a
    ld  (_gb3d_span_x1), a
    jr  _gb3d_r_span_x2
_gb3d_r_span_x1_nonneg:
    cp  #40
    jp  nc, _gb3d_r_draw_span_ret
_gb3d_r_span_x2:
    ld  a, (_gb3d_span_x2)
    cp  #40
    jr  c, _gb3d_r_span_visible
    ld  a, #39
    ld  (_gb3d_span_x2), a
_gb3d_r_span_visible:

    ; BC = (y >> 1) * 32, without a row-offset table.
    ; For y=0..35: low byte = (y & 0x0E) << 4, high = y >> 4.
    ld  a, (_gb3d_current_y)
    ld  b, a
    and #0x0E
    swap a
    ld  c, a
    ld  a, b
    swap a
    and #0x0F
    ld  b, a

    ld  a, (_gb3d_span_x1)
    srl a
    add a, c
    ld  c, a
    ld  a, b
    adc a, #0
    ld  b, a

    ; DE = framebuffer_tilemap + BC
    ld  hl, #_framebuffer_tilemap
    ld  a, (hl+)
    ld  e, a
    ld  a, (hl)
    ld  d, a
    ld  h, d
    ld  l, e
    add hl, bc
    ld  d, h
    ld  e, l

    ; HL = framebuffer_attributes + BC
    ld  hl, #_framebuffer_attributes
    ld  a, (hl+)
    ld  h, (hl)
    ld  l, a
    add hl, bc

    ld  a, (_gb3d_current_y)
    and #1
    jp  nz, _gb3d_r_span_bottom

; ------------------------------------------------------------
; TOP logical row: low nibble
; ------------------------------------------------------------
_gb3d_r_span_top:
    ; odd start => TR only
    ld  a, (_gb3d_span_x1)
    and #1
    jr  z, _gb3d_r_top_pairs
    ld  a, (de)
    and #0xF3
    ld  c, a
    ld  a, (_gb3d_tri_shade)
    add a, a
    add a, a
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    inc de
    inc hl
    ld  a, (_gb3d_span_x1)
    inc a
    ld  (_gb3d_span_x1), a
    ld  c, a
    ld  a, (_gb3d_span_x2)
    cp  c
    ret c

_gb3d_r_top_pairs:
    ; B = full two-pixel pairs
    ld  a, (_gb3d_span_x2)
    ld  c, a
    ld  a, (_gb3d_span_x1)
    ld  b, a
    ld  a, c
    sub b
    inc a
    srl a
    ld  b, a
    ld  a, (_gb3d_tri_pair_top)
    ld  c, a
    ld  a, b
    or  a
    jr  z, _gb3d_r_top_tail
_gb3d_r_top_pair_loop:
    ld  a, (de)
    and #0xF0
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    inc de
    inc hl
    dec b
    jr  nz, _gb3d_r_top_pair_loop

_gb3d_r_top_tail:
    ; odd remaining count => TL only
    ld  a, (_gb3d_span_x2)
    ld  c, a
    ld  a, (_gb3d_span_x1)
    ld  b, a
    ld  a, c
    sub b
    inc a
    and #1
    ret z
    ld  a, (de)
    and #0xFC
    ld  c, a
    ld  a, (_gb3d_tri_shade)
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    ret

; ------------------------------------------------------------
; BOTTOM logical row: high nibble
; ------------------------------------------------------------
_gb3d_r_span_bottom:
    ; odd start => BR only
    ld  a, (_gb3d_span_x1)
    and #1
    jr  z, _gb3d_r_bottom_pairs
    ld  a, (de)
    and #0x3F
    ld  c, a
    ld  a, (_gb3d_tri_shade)
    rrca
    rrca
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    inc de
    inc hl
    ld  a, (_gb3d_span_x1)
    inc a
    ld  (_gb3d_span_x1), a
    ld  c, a
    ld  a, (_gb3d_span_x2)
    cp  c
    ret c

_gb3d_r_bottom_pairs:
    ld  a, (_gb3d_span_x2)
    ld  c, a
    ld  a, (_gb3d_span_x1)
    ld  b, a
    ld  a, c
    sub b
    inc a
    srl a
    ld  b, a
    ld  a, (_gb3d_tri_pair_bottom)
    ld  c, a
    ld  a, b
    or  a
    jr  z, _gb3d_r_bottom_tail
_gb3d_r_bottom_pair_loop:
    ld  a, (de)
    and #0x0F
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    inc de
    inc hl
    dec b
    jr  nz, _gb3d_r_bottom_pair_loop

_gb3d_r_bottom_tail:
    ld  a, (_gb3d_span_x2)
    ld  c, a
    ld  a, (_gb3d_span_x1)
    ld  b, a
    ld  a, c
    sub b
    inc a
    and #1
    ret z
    ld  a, (de)
    and #0xCF
    ld  c, a
    ld  a, (_gb3d_tri_shade)
    swap a
    and #0x30
    or  c
    ld  (de), a
    ld  a, (_gb3d_tri_palette)
    ld  (hl), a
    ret


_gb3d_r_draw_span_ret:
    ret

; ============================================================
; TRIANGLE SETUP HOT PATH
; ============================================================
; Queued vertices have already been projected/clamped by C to:
;   x = -32..71   (edge |dx| <= 103)
;   y = -32..67   (sorted edge dy <= 99)
; and the face collector already rejected flat/back-facing and trivially
; off-screen triangles. That lets the raster entry stay entirely 8-bit.
;
; Input to the divmod helper: A=numerator 0..103, C=denominator 1..99.
; Output: B=quotient, A=remainder. Since numerator bit 7 is always zero, the
; first iteration of a normal 8-bit restoring divide is known to do nothing
; except shift B once. Do that shift up front, then unroll only seven real
; divide rounds.
_gb3d_divmod_edge:
    ld  b, a
    sla b
    xor a

    ; bit 6
    sla b
    rla
    jr  c, _gb3d_r_div_01
    cp  c
    jr  c, _gb3d_r_div_02
_gb3d_r_div_01:
    sub c
    inc b
_gb3d_r_div_02:
    ; bit 5
    sla b
    rla
    jr  c, _gb3d_r_div_03
    cp  c
    jr  c, _gb3d_r_div_04
_gb3d_r_div_03:
    sub c
    inc b
_gb3d_r_div_04:
    ; bit 4
    sla b
    rla
    jr  c, _gb3d_r_div_05
    cp  c
    jr  c, _gb3d_r_div_06
_gb3d_r_div_05:
    sub c
    inc b
_gb3d_r_div_06:
    ; bit 3
    sla b
    rla
    jr  c, _gb3d_r_div_07
    cp  c
    jr  c, _gb3d_r_div_08
_gb3d_r_div_07:
    sub c
    inc b
_gb3d_r_div_08:
    ; bit 2
    sla b
    rla
    jr  c, _gb3d_r_div_09
    cp  c
    jr  c, _gb3d_r_div_10
_gb3d_r_div_09:
    sub c
    inc b
_gb3d_r_div_10:
    ; bit 1
    sla b
    rla
    jr  c, _gb3d_r_div_11
    cp  c
    jr  c, _gb3d_r_div_12
_gb3d_r_div_11:
    sub c
    inc b
_gb3d_r_div_12:
    ; bit 0
    sla b
    rla
    jr  c, _gb3d_r_div_13
    cp  c
    jr  c, _gb3d_r_div_14
_gb3d_r_div_13:
    sub c
    inc b
_gb3d_r_div_14:
    ret

; ============================================================
; void gb3d_draw_triangle_asm(void)
; ============================================================
_gb3d_draw_triangle_asm::
    ; Public/global ABI entry, kept for compatibility and direct callers.
    ld  a, (_gb3d_tri_x0)
    ld  b, a
    ld  a, (_gb3d_tri_y0)
    ld  c, a
    ld  a, (_gb3d_tri_x1)
    ld  d, a
    ld  a, (_gb3d_tri_y1)
    ld  e, a
    ld  a, (_gb3d_tri_x2)
    ld  h, a
    ld  a, (_gb3d_tri_y2)
    ld  l, a
    jr  _gb3d_r_triangle_points_loaded

; Internal queue entry. HL points at x0,y0,x1,y1,x2,y2,material,next.
; Load the six coordinates straight into registers. H/L are also the pointer,
; so x2/y2 briefly live on the stack until the pointer is no longer needed.
_gb3d_draw_triangle_record:
    ld  a, (hl+)
    ld  b, a
    ld  a, (hl+)
    ld  c, a
    ld  a, (hl+)
    ld  d, a
    ld  a, (hl+)
    ld  e, a
    ld  a, (hl+)
    push af
    ld  a, (hl+)
    push af
    ld  a, (hl)
    ld  (_gb3d_tri_material), a
    pop af
    ld  l, a
    pop af
    ld  h, a

_gb3d_r_triangle_points_loaded:
    ; Run the same stable 3-comparison Y sort that used to live in C. Because
    ; projected Y is -32..67, every difference fits signed 8-bit exactly, so
    ; bit 7 of (ya-yb) is a valid sign test with no overflow case.

    ; if (y0 > y1) swap p0,p1
    ld  a, c
    sub e
    jr  z, _gb3d_r_sort_01_done
    bit 7, a
    jr  nz, _gb3d_r_sort_01_done
    ld  a, b
    ld  b, d
    ld  d, a
    ld  a, c
    ld  c, e
    ld  e, a
_gb3d_r_sort_01_done:
    ; if (y1 > y2) swap p1,p2
    ld  a, e
    sub l
    jr  z, _gb3d_r_sort_12_done
    bit 7, a
    jr  nz, _gb3d_r_sort_12_done
    ld  a, d
    ld  d, h
    ld  h, a
    ld  a, e
    ld  e, l
    ld  l, a
_gb3d_r_sort_12_done:
    ; if (y0 > y1) swap p0,p1 again
    ld  a, c
    sub e
    jr  z, _gb3d_r_sort_01b_done
    bit 7, a
    jr  nz, _gb3d_r_sort_01b_done
    ld  a, b
    ld  b, d
    ld  d, a
    ld  a, c
    ld  c, e
    ld  e, a
_gb3d_r_sort_01b_done:

    ; Write sorted points once; the rest of the rasterizer reads these globals.
    ld  a, b
    ld  (_gb3d_tri_x0), a
    ld  a, c
    ld  (_gb3d_tri_y0), a
    ld  a, d
    ld  (_gb3d_tri_x1), a
    ld  a, e
    ld  (_gb3d_tri_y1), a
    ld  a, h
    ld  (_gb3d_tri_x2), a
    ld  a, l
    ld  (_gb3d_tri_y2), a

    ; Unpack the queued material once here instead of shifting/masking it in
    ; the C bucket-flush call and again in the triangle wrapper.
    ld  a, (_gb3d_tri_material)
    ld  b, a
    and #0x07
    ld  (_gb3d_tri_palette), a
    ld  a, b
    rrca
    rrca
    rrca
    and #0x03
    ld  (_gb3d_tri_shade), a

    ; Prebuild two-pixel packed patterns for the span writer.
    ld  b, a
    add a, a
    add a, a
    or  b
    ld  (_gb3d_tri_pair_top), a
    swap a
    and #0xF0
    ld  (_gb3d_tri_pair_bottom), a

    ; Rows are all 8-bit now because projected Y is already bounded.
    ld  a, (_gb3d_tri_y1)
    ld  b, a
    ld  a, (_gb3d_tri_y0)
    ld  c, a
    ld  a, b
    sub c
    ld  (_gb3d_top_rows), a

    ld  a, (_gb3d_tri_y2)
    ld  b, a
    ld  a, (_gb3d_tri_y1)
    ld  c, a
    ld  a, b
    sub c
    inc a
    ld  (_gb3d_bottom_rows), a

    ; --------------------------------------------------------
    ; Long edge: x0,y0 -> x2,y2. This dy is always non-zero because the face
    ; queue only contains positive-area triangles.
    ; --------------------------------------------------------
    ld  a, (_gb3d_tri_y2)
    ld  c, a
    ld  a, (_gb3d_tri_y0)
    ld  e, a
    ld  a, c
    sub e
    ld  (_gb3d_long_dy), a
    ld  c, a

    ld  a, (_gb3d_tri_x2)
    ld  b, a
    ld  a, (_gb3d_tri_x0)
    ld  e, a
    ld  a, b
    sub e
    bit 7, a
    jr  z, _gb3d_r_long_setup_positive
    cpl
    inc a
    ld  e, a
    ld  d, #0xFF
    jr  _gb3d_r_long_setup_div
_gb3d_r_long_setup_positive:
    ld  e, a
    ld  d, #1
_gb3d_r_long_setup_div:
    ld  a, d
    ld  (_gb3d_long_step), a
    ld  a, e
    call _gb3d_divmod_edge
    ld  (_gb3d_long_r), a
    ld  a, d
    bit 7, a
    ld  a, b
    jr  z, _gb3d_r_long_setup_store_q
    cpl
    inc a
_gb3d_r_long_setup_store_q:
    ld  (_gb3d_long_q), a

    ; --------------------------------------------------------
    ; Top short edge: x0,y0 -> x1,y1. A flat top has zero rows, so skip all
    ; q/r work because the top stepper cannot execute in that case.
    ; --------------------------------------------------------
    ld  a, (_gb3d_top_rows)
    or  a
    jr  z, _gb3d_r_bottom_setup_begin
    ld  (_gb3d_top_dy), a
    ld  c, a

    ld  a, (_gb3d_tri_x1)
    ld  b, a
    ld  a, (_gb3d_tri_x0)
    ld  e, a
    ld  a, b
    sub e
    bit 7, a
    jr  z, _gb3d_r_top_setup_positive
    cpl
    inc a
    ld  e, a
    ld  d, #0xFF
    jr  _gb3d_r_top_setup_div
_gb3d_r_top_setup_positive:
    ld  e, a
    ld  d, #1
_gb3d_r_top_setup_div:
    ld  a, d
    ld  (_gb3d_top_step), a
    ld  a, e
    call _gb3d_divmod_edge
    ld  (_gb3d_top_r), a
    ld  a, d
    bit 7, a
    ld  a, b
    jr  z, _gb3d_r_top_setup_store_q
    cpl
    inc a
_gb3d_r_top_setup_store_q:
    ld  (_gb3d_top_q), a

    ; --------------------------------------------------------
    ; Bottom short edge: x1,y1 -> x2,y2. bottom_rows is dy+1. If it is one,
    ; the bottom loop draws its sole row and returns before stepping, so setup
    ; for this edge can be skipped too.
    ; --------------------------------------------------------
_gb3d_r_bottom_setup_begin:
    ld  a, (_gb3d_bottom_rows)
    dec a
    jr  z, _gb3d_r_triangle_state_init
    ld  (_gb3d_bottom_dy), a
    ld  c, a

    ld  a, (_gb3d_tri_x2)
    ld  b, a
    ld  a, (_gb3d_tri_x1)
    ld  e, a
    ld  a, b
    sub e
    bit 7, a
    jr  z, _gb3d_r_bottom_setup_positive
    cpl
    inc a
    ld  e, a
    ld  d, #0xFF
    jr  _gb3d_r_bottom_setup_div
_gb3d_r_bottom_setup_positive:
    ld  e, a
    ld  d, #1
_gb3d_r_bottom_setup_div:
    ld  a, d
    ld  (_gb3d_bottom_step), a
    ld  a, e
    call _gb3d_divmod_edge
    ld  (_gb3d_bottom_r), a
    ld  a, d
    bit 7, a
    ld  a, b
    jr  z, _gb3d_r_bottom_setup_store_q
    cpl
    inc a
_gb3d_r_bottom_setup_store_q:
    ld  (_gb3d_bottom_q), a

_gb3d_r_triangle_state_init:
    ; current_y=y0, both edges start at x0, 8-bit errors=0
    ld  a, (_gb3d_tri_y0)
    ld  (_gb3d_current_y), a
    ld  a, (_gb3d_tri_x0)
    ld  (_gb3d_long_x), a
    ld  (_gb3d_short_x), a
    xor a
    ld  (_gb3d_long_err), a
    ld  (_gb3d_short_err), a

    ; Top half is y0 .. y1-1. Test once before entering instead of loading and
    ; testing rows_left at the top of every iteration.
    ld  a, (_gb3d_top_rows)
    ld  (_gb3d_rows_left), a
    or  a
    jr  z, _gb3d_r_tri_switch_short

_gb3d_r_tri_top_loop:
    call _gb3d_draw_span
    call _gb3d_step_edges_top

    ld  hl, #_gb3d_current_y
    inc (hl)
    ld  hl, #_gb3d_rows_left
    dec (hl)
    jr  nz, _gb3d_r_tri_top_loop

_gb3d_r_tri_switch_short:
    ; short edge becomes y1 -> y2
    ld  a, (_gb3d_tri_x1)
    ld  (_gb3d_short_x), a
    xor a
    ld  (_gb3d_short_err), a

    ; bottom_rows is dy+1, so it is always at least one. Draw first, decrement,
    ; and return immediately on the last row. This avoids two pointless edge
    ; steps after y2 and makes every executed bottom step a non-zero-dy case.
    ld  a, (_gb3d_bottom_rows)
    ld  (_gb3d_rows_left), a

_gb3d_r_tri_bottom_loop:
    call _gb3d_draw_span

    ld  hl, #_gb3d_rows_left
    dec (hl)
    ret z

    call _gb3d_step_edges_bottom
    ld  hl, #_gb3d_current_y
    inc (hl)
    jr  _gb3d_r_tri_bottom_loop

