/*
 * Copyright (c) 2016, Oracle and/or its affiliates.
 *
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without modification, are
 * permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this list of
 * conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice, this list of
 * conditions and the following disclaimer in the documentation and/or other materials provided
 * with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its contributors may be used to
 * endorse or promote products derived from this software without specific prior written
 * permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS
 * OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
 * GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
 * OF THE POSSIBILITY OF SUCH DAMAGE.
 */
package com.oracle.truffle.llvm.parser.bc.impl;

import uk.ac.man.cs.llvm.ir.model.FunctionDeclaration;
import uk.ac.man.cs.llvm.ir.model.FunctionDefinition;
import uk.ac.man.cs.llvm.ir.model.FunctionVisitor;
import uk.ac.man.cs.llvm.ir.model.GlobalAlias;
import uk.ac.man.cs.llvm.ir.model.GlobalConstant;
import uk.ac.man.cs.llvm.ir.model.GlobalVariable;
import uk.ac.man.cs.llvm.ir.model.InstructionBlock;
import uk.ac.man.cs.llvm.ir.model.InstructionVisitor;
import uk.ac.man.cs.llvm.ir.model.MetadataBlock;
import uk.ac.man.cs.llvm.ir.model.MetadataBlock.MetadataReference;
import uk.ac.man.cs.llvm.ir.model.MetadataReferenceType;
import uk.ac.man.cs.llvm.ir.model.Model;
import uk.ac.man.cs.llvm.ir.model.ModelVisitor;
import uk.ac.man.cs.llvm.ir.model.Symbol;
import uk.ac.man.cs.llvm.ir.model.constants.IntegerConstant;
import uk.ac.man.cs.llvm.ir.model.constants.MetadataConstant;
import uk.ac.man.cs.llvm.ir.model.elements.AllocateInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.BinaryOperationInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.BranchInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.CallInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.CastInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.CompareInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.ConditionalBranchInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.ExtractElementInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.ExtractValueInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.GetElementPointerInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.IndirectBranchInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.InsertElementInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.InsertValueInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.LoadInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.PhiInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.ReturnInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.SelectInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.ShuffleVectorInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.StoreInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.SwitchInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.SwitchOldInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.UnreachableInstruction;
import uk.ac.man.cs.llvm.ir.model.elements.VoidCallInstruction;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataFnNode;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataLocalVariable;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataBaseNode;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataBasicType;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataCompositeType;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataDerivedType;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataNode;
import uk.ac.man.cs.llvm.ir.model.metadata.MetadataString;
import uk.ac.man.cs.llvm.ir.types.PointerType;
import uk.ac.man.cs.llvm.ir.types.StructureType;
import uk.ac.man.cs.llvm.ir.types.Type;

/**
 * Parse all those "@llvm.dbg.declare" call instructions and add the fitting Metadata reference to
 * the referenced instruction to allow parsing of those data later one.
 */
public final class LLVMMetadata implements ModelVisitor {

    public static LLVMMetadata generate(Model model) {
        LLVMMetadata visitor = new LLVMMetadata();

        model.accept(visitor);

        return visitor;
    }

    private LLVMMetadata() {
    }

    @Override
    public void visit(GlobalAlias alias) {
    }

    @Override
    public void visit(GlobalConstant constant) {
    }

    @Override
    public void visit(GlobalVariable variable) {
    }

    @Override
    public void visit(FunctionDeclaration function) {
    }

    @Override
    public void visit(FunctionDefinition function) {
        LLVMMetadataFunctionVisitor visitor = new LLVMMetadataFunctionVisitor(function.getMetadata());

        function.accept(visitor);
    }

    @Override
    public void visit(Type type) {
    }

    private static final class LLVMMetadataFunctionVisitor implements FunctionVisitor, InstructionVisitor {
        private InstructionBlock currentBlock = null;

        private final MetadataBlock metadata;

        private LLVMMetadataFunctionVisitor(MetadataBlock metadata) {
            this.metadata = metadata;
        }

        @Override
        public void visit(InstructionBlock block) {
            this.currentBlock = block;
            block.accept(this);
        }

        @Override
        public void visit(AllocateInstruction allocate) {
        }

        /*
         * TODO: metadata seems to be misalign by 8
         *
         * I don't know why, but there is a little misalign between calculated and real metadata id.
         * This has to be solved in the future!
         *
         * Possible issues could probably arrive when there are changes in the number of MDKinds.
         * This has to be evaluated in the future
         */
        private static final int SYMBOL_MISALIGN = 8;

        @Override
        public void visit(VoidCallInstruction call) {
            Symbol callTarget = call.getCallTarget();

            if (callTarget instanceof FunctionDeclaration) {
                if (((FunctionDeclaration) (callTarget)).getName().equals("@llvm.dbg.declare")) {

                    int symbolMetadataId = (int) ((MetadataConstant) call.getArgument(0)).getValue() + SYMBOL_MISALIGN;
                    int symbolIndex = ((MetadataFnNode) metadata.get(symbolMetadataId)).getPointer().getSymbolIndex();
                    long metadataId = ((MetadataConstant) call.getArgument(1)).getValue();

                    Symbol referencedSymbol = currentBlock.getFunctionSymbols().getSymbol(symbolIndex);

                    // TODO: use visitor pattern
                    // TODO: parse global variables
                    if (referencedSymbol instanceof AllocateInstruction) {
                        Type symType = ((AllocateInstruction) referencedSymbol).getPointeeType();
                        if (symType instanceof MetadataReferenceType) {
                            MetadataReferenceType metadataRefType = (MetadataReferenceType) symType;

                            // TODO: other variables than localVar should be possible here
                            MetadataLocalVariable localVar = (MetadataLocalVariable) metadata.getReference(metadataId).get();
                            MetadataReference typeReference = localVar.getType();
                            while (typeReference.get() instanceof MetadataDerivedType) {
                                MetadataDerivedType derivedType = (MetadataDerivedType) typeReference.get();

                                if (!derivedType.isOnlyReference()) {
                                    break;
                                }

                                typeReference = derivedType.getBaseType();
                            }
                            metadataRefType.setValidatedMetadataReference(typeReference);
                        }
                    }
                }
            }
        }

        @Override
        public void visit(BinaryOperationInstruction operation) {
        }

        @Override
        public void visit(BranchInstruction branch) {
        }

        @Override
        public void visit(CallInstruction call) {
        }

        @Override
        public void visit(CastInstruction cast) {
        }

        @Override
        public void visit(CompareInstruction operation) {
        }

        @Override
        public void visit(ConditionalBranchInstruction branch) {
        }

        @Override
        public void visit(ExtractElementInstruction extract) {
        }

        @Override
        public void visit(ExtractValueInstruction extract) {
        }

        private static void setElementPointerName(GetElementPointerInstruction gep, MetadataDerivedType element) {
            if (element.getName().isPresent()) {
                gep.setReferenceName(((MetadataString) element.getName().get()).getString());
            }
        }

        private static void setElementPointerName(GetElementPointerInstruction gep, MetadataCompositeType element) {
            if (element.getName().isPresent()) {
                gep.setReferenceName(((MetadataString) element.getName().get()).getString());
            }
        }

        private static void setElementPointerName(GetElementPointerInstruction gep, MetadataBaseNode element) {
            if (element instanceof MetadataDerivedType) {
                setElementPointerName(gep, (MetadataDerivedType) element);
            } else if (element instanceof MetadataCompositeType) {
                setElementPointerName(gep, (MetadataCompositeType) element);
            }
        }

        private static long getOffset(MetadataBaseNode element) {
            // TODO: simplify design by using interfaces/abstract classes
            if (element instanceof MetadataDerivedType) {
                return ((MetadataDerivedType) element).getOffset();
            } else if (element instanceof MetadataCompositeType) {
                return ((MetadataCompositeType) element).getOffset();
            }
            throw new AssertionError("unknow node type: " + element);
        }

        private static void parseCompositeTypeStruct(GetElementPointerInstruction gep, StructureType struct, MetadataCompositeType node) {
            struct.setName(((MetadataString) node.getName().get()).getString());

            MetadataNode elements = (MetadataNode) node.getMemberDescriptors().get();

            Symbol idx = gep.getIndices().get(1);
            int parsedIndex = idx instanceof IntegerConstant ? (int) ((IntegerConstant) (idx)).getValue() : 0;

            long elementOffset = struct.getElementOffset(parsedIndex);
            for (MetadataReference element : elements) {
                if (getOffset(element.get()) == elementOffset) {
                    setElementPointerName(gep, element.get());
                    break;
                }
            }
        }

        private static void parseCompositeTypeStructBitcast(GetElementPointerInstruction gep, CastInstruction cast, MetadataCompositeType node) {
            MetadataNode elements = (MetadataNode) node.getMemberDescriptors().get();

            Symbol idx = gep.getIndices().get(0);
            int parsedIndex = idx instanceof IntegerConstant ? (int) ((IntegerConstant) (idx)).getValue() : 0;

            // TODO: correct sizeof?
            int elementOffset = parsedIndex * cast.getType().sizeof();

            for (int i = 0; i < elements.size(); i++) {
                MetadataBaseNode element = elements.get(i).get();

                if (getOffset(element) == elementOffset) {
                    setElementPointerName(gep, element);
                    break;
                }
            }
        }

        @Override
        public void visit(GetElementPointerInstruction gep) {
            Type t1 = ((PointerType) (gep.getBasePointer().getType())).getPointeeType();
            if (t1 instanceof StructureType) {
                StructureType thisStruct = (StructureType) t1;
                // TODO: should always be this type?
                if (!thisStruct.getMetadataReference().isPresent()) {
                    return;
                }
                MetadataBaseNode node = thisStruct.getMetadataReference().get();

                if (node instanceof MetadataCompositeType) {
                    parseCompositeTypeStruct(gep, thisStruct, (MetadataCompositeType) node);
                } else if (node instanceof MetadataBasicType) {
                    // TODO: implement?
                } else if (node instanceof MetadataDerivedType) {
                    // TODO: type check
                    MetadataCompositeType compNode = (MetadataCompositeType) ((MetadataDerivedType) node).getBaseType().get();
                    parseCompositeTypeStruct(gep, thisStruct, compNode);
                } else {
                    throw new AssertionError("unknow node type: " + node);
                }

            } else if (gep.getBasePointer() instanceof CastInstruction) {
                CastInstruction cast = (CastInstruction) gep.getBasePointer();
                Symbol value = cast.getValue();

                if (!(value instanceof AllocateInstruction)) {
                    return;
                }
                AllocateInstruction allocate = (AllocateInstruction) value;

                Type symType = allocate.getPointeeType();
                if (!(symType instanceof StructureType)) {
                    return;
                }
                StructureType thisStruct = (StructureType) symType;

                if (!thisStruct.getMetadataReference().isPresent()) {
                    return;
                }
                MetadataBaseNode node = thisStruct.getMetadataReference().get();

                if (node instanceof MetadataCompositeType) {
                    parseCompositeTypeStructBitcast(gep, cast, (MetadataCompositeType) node);
                } else if (node instanceof MetadataBasicType) {
                    // TODO: implement?
                } else if (node instanceof MetadataDerivedType) {
                    // TODO: type check
                    MetadataCompositeType compNode = (MetadataCompositeType) ((MetadataDerivedType) node).getBaseType().get();
                    parseCompositeTypeStructBitcast(gep, cast, compNode);
                } else {
                    throw new AssertionError("unknow node type: " + node);
                }
            }
        }

        @Override
        public void visit(IndirectBranchInstruction branch) {
        }

        @Override
        public void visit(InsertElementInstruction insert) {
        }

        @Override
        public void visit(InsertValueInstruction insert) {
        }

        @Override
        public void visit(LoadInstruction load) {
        }

        @Override
        public void visit(PhiInstruction phi) {
        }

        @Override
        public void visit(ReturnInstruction ret) {
        }

        @Override
        public void visit(SelectInstruction select) {
        }

        @Override
        public void visit(ShuffleVectorInstruction shuffle) {
        }

        @Override
        public void visit(StoreInstruction store) {
        }

        @Override
        public void visit(SwitchInstruction select) {
        }

        @Override
        public void visit(SwitchOldInstruction select) {
        }

        @Override
        public void visit(UnreachableInstruction unreachable) {
        }
    }
}
