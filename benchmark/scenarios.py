"""
Benchmark scenarios — simulates a curious user reading articles and asking questions.

Each scenario is a list of reading sessions. Each session has:
- A passage (real article text)
- A sequence of interactions: (selected_text, question) pairs

The user reads 2-3 articles across different topics, asking 10+ questions total,
then switches to a new context to test cross-topic concept linking.
"""

SCENARIO_1 = {
    "name": "Biology student exploring cells and evolution",
    "profile": "I'm a second-year biology undergrad. Comfortable with basic cell biology but new to molecular genetics. I like visual analogies.",
    "sessions": [
        {
            "title": "Cell Biology — Mitochondria",
            "passage": """The mitochondrion is a double-membraned organelle found in most eukaryotic organisms. Mitochondria generate most of the cell's supply of adenosine triphosphate (ATP), used as a source of chemical energy. They were first discovered by Albert von Kolliker in 1857.

The organelle is composed of compartments that carry out specialized functions. These compartments include the outer membrane, the intermembrane space, the inner membrane, and the cristae and matrix. Mitochondrial proteins vary depending on the tissue and the species.

The endosymbiotic hypothesis suggests that mitochondria were originally prokaryotic cells, capable of implementing oxidative mechanisms that were not possible for eukaryotic cells. They became endosymbionts living inside the eukaryote. The evidence for this includes the fact that mitochondria have their own DNA (mtDNA), which is circular like bacterial DNA.

The electron transport chain is a series of complexes that transfer electrons from electron donors to electron acceptors via redox reactions, and couples this electron transfer with the transfer of protons across a membrane. This creates an electrochemical proton gradient that drives the synthesis of ATP via ATP synthase.""",
            "interactions": [
                ("mitochondrion is a double-membraned organelle", "What makes the double membrane important?"),
                ("adenosine triphosphate (ATP)", "How exactly does ATP store energy?"),
                ("endosymbiotic hypothesis", "What evidence supports the endosymbiotic theory?"),
                ("mitochondria have their own DNA (mtDNA), which is circular like bacterial DNA", "Why is circular DNA evidence for endosymbiosis?"),
                ("electron transport chain", "Can you walk me through the electron transport chain step by step?"),
                ("electrochemical proton gradient", "What is a proton gradient and why does it matter?"),
            ],
        },
        {
            "title": "Evolution — Natural Selection",
            "passage": """Natural selection is the differential survival and reproduction of individuals due to differences in phenotype. It is a key mechanism of evolution, the change in the heritable traits characteristic of a population over generations. Charles Darwin popularised the term in his 1859 book On the Origin of Species.

Variation exists within all populations of organisms. This occurs partly because random mutations arise in the genome of an individual organism, and their offspring can inherit such mutations. Throughout the lives of the individuals, their genomes interact with their environments to cause variations in traits.

Individuals with certain variants of the trait may survive and reproduce more than individuals with other, less successful variants. Therefore the population evolves. Factors that affect reproductive success are also important, an aspect of selection known as sexual selection.

Natural selection acts on the phenotype, the characteristics of the organism which actually interact with the environment, but the genetic basis of any phenotype that gives a reproductive advantage may become more common in a population. Over time, this process can result in populations that specialise for particular ecological niches and may eventually result in speciation.""",
            "interactions": [
                ("differential survival and reproduction", "How does differential survival actually lead to evolution?"),
                ("random mutations arise in the genome", "Are all mutations random? What about epigenetics?"),
                ("Individuals with certain variants of the trait may survive and reproduce more", "Can you give me a concrete example of this in nature?"),
                ("sexual selection", "How is sexual selection different from natural selection?"),
                ("specialise for particular ecological niches", "What's the connection between niches and speciation?"),
            ],
        },
        {
            "title": "Cross-topic — connecting biology concepts",
            "passage": """Modern biology reveals deep connections between cellular processes and evolutionary theory. The mitochondria in your cells are themselves evidence of evolution — ancient bacteria that became permanent residents inside larger cells billions of years ago. This endosymbiotic event was a major evolutionary transition.

Similarly, the genetic code itself — DNA, RNA, and the machinery of protein synthesis — is shared across all known life, pointing to common ancestry. Mutations in DNA, the raw material for natural selection, occur during cell division when DNA replication is imperfect.""",
            "interactions": [
                ("mitochondria in your cells are themselves evidence of evolution", "How does the endosymbiotic theory connect to natural selection?"),
                ("Mutations in DNA, the raw material for natural selection, occur during cell division", "So cell division errors drive evolution? That's amazing."),
                (None, "Looking back at everything I've read, what's the biggest connection between cell biology and evolution?"),
            ],
        },
    ],
}

SCENARIO_2 = {
    "name": "CS student learning machine learning",
    "profile": "I'm a computer science senior. Strong in linear algebra and programming. New to deep learning. I prefer math-first explanations with code examples.",
    "sessions": [
        {
            "title": "Neural Networks — Backpropagation",
            "passage": """A neural network is a network of artificial neurons organized in layers. Each connection between neurons has a weight that is adjusted during training. The network learns by adjusting these weights to minimize a loss function.

Backpropagation is the algorithm used to train neural networks. It computes the gradient of the loss function with respect to each weight by applying the chain rule, computing the gradient one layer at a time, iterating backward from the last layer.

The gradient tells us how much the loss would change if we made a small change to each weight. We then update each weight in the direction that reduces the loss — this is gradient descent. The learning rate controls how big each step is.

Stochastic gradient descent (SGD) computes the gradient using a random subset (mini-batch) of the training data rather than the entire dataset, which makes training much faster while still converging to a good solution.""",
            "interactions": [
                ("adjusted during training", "What does 'training' actually mean mathematically?"),
                ("chain rule, computing the gradient one layer at a time", "Can you show me the chain rule applied to a simple 2-layer network?"),
                ("gradient descent", "What's the intuition behind gradient descent?"),
                ("learning rate controls how big each step is", "What happens if the learning rate is too high or too low?"),
                ("Stochastic gradient descent", "Why does using random mini-batches still work?"),
            ],
        },
        {
            "title": "Transformers — Attention Mechanism",
            "passage": """The Transformer is a deep learning architecture that relies entirely on self-attention mechanisms, dispensing with recurrence and convolutions entirely. It was introduced in the paper "Attention Is All You Need" by Vaswani et al. in 2017.

Self-attention allows the model to weigh the importance of different words in a sequence when encoding each word. For each word, the model computes Query (Q), Key (K), and Value (V) vectors. The attention score between two words is the dot product of the query of one word with the key of the other, scaled by the square root of the dimension.

Multi-head attention runs multiple attention computations in parallel, each with different learned projections. This allows the model to attend to information from different representation subspaces at different positions.

The Transformer architecture uses positional encoding to inject information about the position of tokens in the sequence, since the self-attention mechanism is permutation-invariant.""",
            "interactions": [
                ("relies entirely on self-attention mechanisms", "Why was removing recurrence such a big deal?"),
                ("Query (Q), Key (K), and Value (V) vectors", "What's the intuition behind Q, K, V? Why three separate vectors?"),
                ("dot product of the query of one word with the key of the other", "Can you show the attention computation with actual numbers?"),
                ("Multi-head attention", "Why do we need multiple heads? What does each one learn?"),
                ("positional encoding", "How does positional encoding work? Why sine/cosine?"),
                (None, "How do transformers relate to the backpropagation we studied earlier?"),
            ],
        },
    ],
}

ALL_SCENARIOS = [SCENARIO_1, SCENARIO_2]
