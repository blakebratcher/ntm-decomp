// Export all defined instructions and their control-flow edges, not just functions.
// Outputs are local analysis evidence; no ROM bytes are printed or committed.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import java.io.*;
import java.nio.file.*;

public class NtmExport extends GhidraScript {
    boolean rom(long a) { return (a >= 0 && a < 0x100000) || (a >= 0x200000 && a < 0x300000); }
    public void run() throws Exception {
        Path dir = Paths.get(getScriptArgs()[0]); Files.createDirectories(dir);
        long count = 0, bytes = 0;
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(dir.resolve("instructions.tsv")))) {
            out.println("address\tlength\tflow\tfallthrough\ttargets\tfunction\tinstruction");
            for (Instruction ins : currentProgram.getListing().getInstructions(true)) {
                long a = ins.getAddress().getOffset(); if (!rom(a)) continue;
                StringBuilder targets = new StringBuilder();
                for (Address dst : ins.getFlows()) {
                    if (targets.length() > 0) targets.append(',');
                    targets.append(String.format("%08x", dst.getOffset()));
                }
                Function f = getFunctionContaining(ins.getAddress());
                Address fall = ins.getFallThrough();
                out.printf("%08x\t%d\t%s\t%s\t%s\t%s\t%s%n", a, ins.getLength(),
                    ins.getFlowType(), fall == null ? "" : String.format("%08x", fall.getOffset()),
                    targets.toString(), f == null ? "" : f.getEntryPoint().toString(), ins.toString());
                count++; bytes += ins.getLength();
            }
        }
        println("NTM exported instructions=" + count + " bytes=" + bytes);
    }
}
