import os
import chromadb
from chromadb.utils import embedding_functions
import anthropic

# 1. Initialize ChromaDB Persistent Client & Default Embedding Function
from pathlib import Path

# kept under data/ (not wherever the server happened to be started)
db_client = chromadb.PersistentClient(path=str(Path(__file__).resolve().parents[2] / "data" / "rag_chroma_db"))
embedding_fn = embedding_functions.DefaultEmbeddingFunction()

collection = db_client.get_or_create_collection(
    name="comprehensive_hardware_repair_kb",
    embedding_function=embedding_fn
)

# 2. Define an Extensive Knowledge Base (8+ Detailed Repair Guides)
repair_documents = [
    {
        "id": "doc_spacebar_stabilizer_screws",
        "text": (
            "How to replace a space key that uses a stabilizer bar and hidden screws: "
            "1. Safety first: Always unplug the keyboard completely or remove batteries before opening electronics. "
            "2. Removing the spacebar keycap: Gently pull up evenly using a wire keycap puller. Be careful not to bend the metal wire stabilizer bar (Costar or Cherry style) attached underneath the keycap hooks. "
            "3. Locating hidden screws: Many keyboards hide assembly screws underneath rubber feet pads, serial number stickers, or fold-out tilt legs. Peel these back carefully. "
            "4. Unscrewing the case: Use a precision Phillips screwdriver to remove all case screws. Keep track of screw lengths. "
            "5. Splitting the case: Carefully separate top and bottom plastic case bezels using a plastic opening tool. Watch out for internal ribbon cables connecting the daughterboard or lights—do not yank them."
        ),
        "metadata": {"category": "keyboards", "component": "spacebar", "difficulty": "medium"}
    },
    {
        "id": "doc_standard_keycaps",
        "text": (
            "How to replace standard mechanical or membrane keys: "
            "1. Power down and disconnect the device. "
            "2. Position a wire keycap puller around opposite corners of the target keycap. "
            "3. Pull straight upward with steady, even force until the keycap snaps off the cross-shaped stem. Avoid twisting or yanking sideways to prevent breaking the switch stem. "
            "4. To install a new keycap, align it directly over the switch stem and press down firmly until it seats completely."
        ),
        "metadata": {"category": "keyboards", "component": "keycaps", "difficulty": "easy"}
    },
    {
        "id": "doc_hotswap_switches",
        "text": (
            "How to replace a broken switch on a hot-swappable mechanical keyboard: "
            "1. Unplug the keyboard. Remove the keycap covering the faulty switch using a keycap puller. "
            "2. Take a metal switch puller tool and clip its prongs into the top and bottom clips of the mechanical switch housing. "
            "3. Squeeze gently and pull straight up with steady vertical force. Do not rock or bend the switch violently, as this can damage the hot-swap metal sockets on the PCB. "
            "4. Inspect the pins of the replacement switch to ensure they are completely straight. Align the pins with the PCB socket holes and push straight down until it clicks securely into place."
        ),
        "metadata": {"category": "keyboards", "component": "switches", "difficulty": "easy"}
    },
    {
        "id": "doc_soldered_switches",
        "text": (
            "How to desolder and replace a broken switch on a soldered mechanical keyboard: "
            "1. Disconnect the keyboard, unscrew the case, and safely detach the main circuit board (PCB) from the plate. "
            "2. Heat your soldering iron to ~350°C (660°F). Apply a touch of fresh solder to the two switch pins to improve heat transfer. "
            "3. Place a solder sucker (desoldering pump) or desoldering braid directly over the molten solder joint, trigger the pump, and suction out the old solder completely. "
            "4. Once both pins are free of solder, gently wiggle or pull the switch from the top side while applying mild heat if necessary. Clean the pad, insert the new switch, and solder the pins back down."
        ),
        "metadata": {"category": "keyboards", "component": "switches", "difficulty": "advanced"}
    },
    {
        "id": "doc_liquid_spills",
        "text": (
            "How to fix sticky keys caused by liquid spills: "
            "1. Immediate action: Disconnect the USB cable or power source instantly. Flip the keyboard upside down immediately to prevent liquid from seeping into the main controller PCB. "
            "2. Key removal: Remove all sticky keycaps surrounding the spill zone. "
            "3. Cleaning: Use a cotton swab saturated with high-purity isopropyl alcohol (90% or higher) to clean dried residue around and inside the switch housing. "
            "4. Drying: Leave the keyboard completely inverted and disassembled in a well-ventilated area for at least 24 to 48 hours before testing or reconnecting power."
        ),
        "metadata": {"category": "maintenance", "component": "spills", "difficulty": "medium"}
    },
    {
        "id": "doc_mouse_micro_switch",
        "text": (
            "How to replace a mouse double-clicking micro-switch: "
            "1. Unplug the mouse. Peel off the adhesive mouse skates (feet) carefully to expose hidden assembly screws underneath. "
            "2. Unscrew the housing and lift the top shell away, taking care not to strain any internal side-button wires. "
            "3. Locate the faulty mechanical micro-switch (such as an Omron switch) on the circuit board. "
            "4. Desolder the three metal pins of the old micro-switch using a soldering iron and a solder sucker or desoldering braid. "
            "5. Insert the new micro-switch into the PCB holes, solder the pins securely, reassemble the mouse shell, and apply fresh replacement mouse skates."
        ),
        "metadata": {"category": "mice", "component": "micro_switch", "difficulty": "advanced"}
    },
    {
        "id": "doc_mouse_scroll_wheel",
        "text": (
            "How to fix a jumping or skipping mouse scroll wheel: "
            "1. Disconnect the mouse, remove the mouse skates, and open the outer plastic shell. "
            "2. Locate the mechanical scroll wheel encoder (the small metal box-like component the wheel passes through). "
            "3. Spray a tiny, controlled burst of specialized electrical contact cleaner (like DeoxIT D5) directly inside the small gaps of the encoder housing. Avoid using WD-40 or general lubricants which attract dust. "
            "4. Rotate the scroll wheel rapidly back and forth for 30 seconds to clean the internal copper wiper contacts, let it dry completely, and reassemble."
        ),
        "metadata": {"category": "mice", "component": "scroll_wheel", "difficulty": "medium"}
    },
    {
        "id": "doc_ribbon_cable_handling",
        "text": (
            "Safe handling of internal ribbon cables (FPC connectors): "
            "1. Never pull or yank flat plastic ribbon cables directly out of their connectors; this will tear the traces or bend locking pins. "
            "2. Locate the locking mechanism on the connector socket: flip-up black/brown latch bars or sliding push-bars. "
            "3. Gently unlock the latch using a fine plastic spudger or fingernail. The ribbon cable will slide out effortlessly with zero resistance. "
            "4. When reconnecting, push the ribbon cable straight and flat into the slot until fully seated, then snap the locking latch back down securely."
        ),
        "metadata": {"category": "general", "component": "ribbon_cables", "difficulty": "easy"}
    },
    {
        "id": "doc_fuse_identify_test",
        "text": (
            "How to find and test a blown fuse: "
            "1. Safety first: unplug the device from the wall, remove batteries, and for a car switch the ignition off and remove the key. Wait a minute for capacitors to discharge. "
            "2. Locate the fuse: in a UK plug it sits under the plug cover between the pins; in appliances look for a fuse holder near the power inlet; in cars check the fuse box diagram in the lid or manual. "
            "3. Read the rating printed on the fuse cap or body: current in amps (for example T2A or F500mA), voltage (250V), and speed letter (F fast, T slow-blow). "
            "4. Glass fuses with a broken or blackened wire are blown. Ceramic and SMD fuses look fine when blown, so test them. "
            "5. Test with a multimeter in continuity mode, device unplugged: a good fuse beeps or reads near 0 ohms; a blown fuse reads open or OL."
        ),
        "metadata": {"category": "fuses", "component": "fuse_test", "difficulty": "easy"}
    },
    {
        "id": "doc_fuse_replace",
        "text": (
            "How to replace a fuse safely: "
            "1. Confirm the device is unplugged or the ignition is off before touching the holder. "
            "2. Remove the old fuse with plastic tweezers or a fuse puller, never a metal screwdriver or pliers. Twist-off holders: press in and turn a quarter turn anticlockwise. "
            "3. The replacement must match physical size, amps exactly, voltage equal or higher, the same speed letter (F or T), and breaking capacity where marked. Never fit a higher amp fuse and never bridge the holder with wire, foil or a paperclip. "
            "4. Seat the new fuse centered and gently between the clips until it sits flat. Do not force it; if it will not fit it is the wrong size. "
            "5. Close the holder or plug cover fully before reconnecting power. If the new fuse blows straight away, stop: the device has a fault a fuse cannot fix."
        ),
        "metadata": {"category": "fuses", "component": "fuse_replace", "difficulty": "easy"}
    },
    {
        "id": "doc_fuse_types",
        "text": (
            "Common fuse types and ratings: "
            "UK plug fuses (BS 1362): 3 A for appliances up to about 700 W, 13 A above that, or the value in the appliance instructions. "
            "Car blade fuses: use the same amp rating and blade size (mini, standard, maxi). Colours: 5 A tan, 7.5 A brown, 10 A red, 15 A blue, 20 A yellow, 25 A clear, 30 A green. "
            "Glass cartridge fuses: 5x20 mm and 6.3x32 mm are the common sizes; check length and diameter. "
            "Resettable polyfuses (PTC) on USB keyboards and mice reset by themselves after unplugging and cooling for a few minutes; repeated tripping means a cable, connector or board short."
        ),
        "metadata": {"category": "fuses", "component": "fuse_types", "difficulty": "easy"}
    },
]

# Guides that must always be in the prompt for a demo, whatever the semantic search returns.
ALWAYS_INCLUDE_BY_DEMO = {
    "fuse_replacement": ["doc_fuse_replace"],
}
# 3. Add Documents to ChromaDB. upsert so guides added later (e.g. the fuse guides) reach an existing database.
collection.upsert(
    ids=[doc["id"] for doc in repair_documents],
    documents=[doc["text"] for doc in repair_documents],
    metadatas=[doc["metadata"] for doc in repair_documents]
)


def retrieve_repair_knowledge(query: str, top_k: int = 2, always_include: list[str] | None = None,
                              category: str | None = None) -> str:
    """The guides most relevant to `query`, as text for a prompt (no Claude call: Pass 2 adds them to its own).

    `always_include`: guide ids that are always added first. `category`: search that metadata category first,
    then fill any remaining slots from the whole collection (so a category with few guides never returns nothing)."""
    chunks: list[str] = []
    seen: set[str] = set()

    def add(ids, docs):
        for i, d in zip(ids, docs):
            if i not in seen and d:
                seen.add(i)
                chunks.append(d)

    if always_include:
        got = collection.get(ids=list(always_include))
        add(got.get("ids") or [], got.get("documents") or [])
    if query.strip():
        if category:
            try:
                r = collection.query(query_texts=[query], n_results=top_k, where={"category": category})
                add(r["ids"][0], r["documents"][0])
            except Exception:
                pass
        if len(chunks) < top_k:
            r = collection.query(query_texts=[query], n_results=top_k + len(seen))
            add(r["ids"][0], r["documents"][0])
    return "\n\n---\n\n".join(chunks[:max(top_k, len(always_include or []))])


# 4. Setup RAG Query Function with Claude
anthropic_client = anthropic.Anthropic()

def query_repair_rag(user_question: str, top_k: int = 3):
    """Searches ChromaDB for relevant repair instructions, builds prompt context, and queries Claude."""
    
    # Query ChromaDB for semantically similar chunks
    results = collection.query(
        query_texts=[user_question],
        n_results=top_k
    )
    
    retrieved_chunks = results["documents"][0] if results["documents"] else []
    
    print(f"\n[RAG] Retrieved {len(retrieved_chunks)} relevant chunks from ChromaDB.")
    
    # Format context for LLM
    context_text = "\n\n---\n\n".join(retrieved_chunks)
    
    system_prompt = (
        "You are an expert hardware repair assistant. Answer the user's question "
        "strictly and accurately using the provided repair documentation context. "
        "Emphasize safety steps (like unplugging first) and detail specific mechanical actions."
    )
    
    user_prompt = f"""Repair Documentation Context:
{context_text}

User Question: {user_question}"""

    # Call Claude API
    response = anthropic_client.messages.create(
        model=os.getenv("RAG_MODEL", "claude-sonnet-5-5"),
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    
    return response.content[0].text, retrieved_chunks


# --- Example Test Execution ---
if __name__ == "__main__":
    test_query = "My mouse scroll wheel is skipping pages when I scroll. How do I fix it?"
    
    answer, sources = query_repair_rag(test_query, top_k=2)
    
    print("\n=== AI Repair Answer ===")
    print(answer)