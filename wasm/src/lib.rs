// The reference BLAKE3 crate, compiled to wasm32, exposing the subtree entry points
// Peergos needs. Deliberately not wasm-bindgen: a raw module with one exported memory is
// a smaller artefact, needs no generated JS glue, and leaves the copy into linear memory
// visible so the benchmark can charge it to this candidate.
//
// The input buffer is a single preallocated region, reused for every chunk, which is what
// blake3.md suggests instead of allocating per chunk.

#![no_std]

use blake3::hazmat::{merge_subtrees_non_root, merge_subtrees_root, HasherExt, Mode};
use blake3::{Hash, Hasher};

/// One 4 MiB Peergos chunk, plus a little slack so 5 MiB legacy chunks also fit.
const BUF_LEN: usize = 5 * 1024 * 1024;

static mut INPUT: [u8; BUF_LEN] = [0; BUF_LEN];
/// Output area: a 32 byte chaining value or hash.
static mut OUTPUT: [u8; 32] = [0; 32];
/// Scratch for merges: two input CVs.
static mut MERGE_IN: [u8; 64] = [0; 64];

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

/// Where the caller should write the bytes to hash.
#[no_mangle]
pub extern "C" fn input_ptr() -> *const u8 {
    unsafe { INPUT.as_ptr() }
}

#[no_mangle]
pub extern "C" fn input_capacity() -> usize {
    BUF_LEN
}

/// Where a result appears after any of the calls below.
#[no_mangle]
pub extern "C" fn output_ptr() -> *const u8 {
    unsafe { OUTPUT.as_ptr() }
}

/// Where the caller writes the two chaining values to merge.
#[no_mangle]
pub extern "C" fn merge_input_ptr() -> *const u8 {
    unsafe { MERGE_IN.as_ptr() }
}

fn put_output(bytes: &[u8; 32]) {
    unsafe {
        OUTPUT.copy_from_slice(bytes);
    }
}

/// The chaining value of the aligned subtree of `len` bytes starting at chunk
/// `chunk_index`, i.e. at input offset chunk_index * 1024. This is the call an upload
/// makes per 4 MiB chunk.
#[no_mangle]
pub extern "C" fn subtree_cv(len: usize, chunk_index_low: u32, chunk_index_high: u32) {
    let chunk_index = ((chunk_index_high as u64) << 32) | chunk_index_low as u64;
    let input = unsafe { &INPUT[..len] };
    let mut hasher = Hasher::new();
    hasher.set_input_offset(chunk_index * 1024);
    hasher.update(input);
    let cv = hasher.finalize_non_root();
    put_output(&cv);
}

/// Merge two subtree chaining values into their parent's chaining value.
#[no_mangle]
pub extern "C" fn merge_non_root(left_len: u64, right_len: u64) {
    let (left, right) = split_merge_input();
    let cv = merge_subtrees_non_root(&left, &right, Mode::Hash);
    put_output(&cv);
    let _ = (left_len, right_len);
}

/// Merge the top two chaining values into the hash of the whole input.
#[no_mangle]
pub extern "C" fn merge_root() {
    let (left, right) = split_merge_input();
    let hash: Hash = merge_subtrees_root(&left, &right, Mode::Hash);
    put_output(hash.as_bytes());
}

fn split_merge_input() -> (blake3::hazmat::ChainingValue, blake3::hazmat::ChainingValue) {
    let raw = unsafe { &MERGE_IN };
    let mut left = [0u8; 32];
    let mut right = [0u8; 32];
    left.copy_from_slice(&raw[..32]);
    right.copy_from_slice(&raw[32..]);
    (left, right)
}

/// The ordinary whole-input hash, for checking against the test vectors.
#[no_mangle]
pub extern "C" fn hash_all(len: usize) {
    let input = unsafe { &INPUT[..len] };
    let mut hasher = Hasher::new();
    hasher.update(input);
    put_output(hasher.finalize().as_bytes());
}
