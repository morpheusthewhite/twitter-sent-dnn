"""
CNN for sentence modeling described in:
A Convolutional Neural Network for Modeling Sentence
"""
import sys, os, time
import pdb

import math, random
import numpy as np
import theano
import theano.tensor as T
import util

THEANO_COMPILE_MODE = "FAST_RUN"

from logreg import LogisticRegression
class WordEmbeddingLayer(object):
    """
    Layer that takes input vectors, output the sentence matrix
    """
    def __init__(self, rng, 
                 input,
                 vocab_size, 
                 embed_dm, 
                 embeddings = None,
    ):
        """
        input: theano.tensor.dmatrix, (number of instances, sentence word number)
        
        vocab_size: integer, the size of vocabulary,

        embed_dm: integer, the dimension of word vector representation

        embeddings: theano.tensor.TensorType
        pretrained embeddings
        """                
        if embeddings:
            print "use pretrained embeddings"
            assert embeddings.get_value().shape == (vocab_size, embed_dm), "%r != %r" %(
                embeddings.get_value().shape, 
                (vocab_size, embed_dm)
            )
            
            self.embeddings = embeddings
        else:
            embedding_val = np.asarray(
                rng.uniform(low = -0.5, high = 0.5, size = (vocab_size, embed_dm)), 
                dtype = theano.config.floatX
            )
            
            embedding_val[vocab_size-1,:] = 0 # the <PADDING> character is intialized to 0
            
            self.embeddings = theano.shared(
                np.asarray(embedding_val, 
                           dtype = theano.config.floatX),
                borrow = True,
                name = 'embeddings'
            )

        
        self.params = [self.embeddings]
        
        self.param_shapes = [(vocab_size, embed_dm)]
        
        # Return:
        
        # :type, theano.tensor.tensor4
        # :param, dimension(1, 1, word embedding dimension, number of words in sentence)
        #         made to be 4D to fit into the dimension of convolution operation
        sent_embedding_list, updates = theano.map(lambda sent: self.embeddings[sent], 
                                                  input)
        sent_embedding_tensor = T.stacklists(sent_embedding_list) # make it into a 3D tensor
        
        self.output = sent_embedding_tensor.dimshuffle(0, 'x', 2, 1) # make it a 4D tensor
                    
class ConvFoldingPoolLayer(object):
    """
    Convolution, folding and k-max pooling layer
    """
    def __init__(self, 
                 rng, 
                 input,
                 filter_shape,
                 k,
                 activation,
                 fan_in_fan_out = True,
                 fold = 0,
                 W = None,
                 b = None):
        """
        rng: numpy random number generator
        input: theano.tensor.tensor4
               the sentence matrix, (number of instances, number of input feature maps,  embedding dimension, number of words)
        
        filter_shape: tuple of length 4, 
           dimension: (number of filters, num input feature maps, filter height, filter width)
        
        k: int or theano.tensor.iscalar,
           the k value in the max-pooling layer

        activation: str
           the activation unit type, `tanh` or `relu` or 'sigmoid'

        fan_in_fan_out: bool
           whether use fan-in fan-out initialization or not. Default, True
           If not True, use `normal(0, 0.05, size)`

        fold: int, 0 or 1
           fold or not

        W: theano.tensor.tensor4,
           the filter weight matrices, 
           dimension: (number of filters, num input feature maps, filter height, filter width)

        b: theano.tensor.vector,
           the filter bias, 
           dimension: (filter number, )
                
        """
        
        self.input = input
        self.k = k
        self.filter_shape = filter_shape
        self.fold = fold

        assert activation in ('tanh', 'relu', 'sigmoid')
        self.activation = activation
        
        if W is not None:
            self.W = W
        else:
            if fan_in_fan_out:
                # use fan-in fan-out init
                fan_in = np.prod(filter_shape[1:])
                
                fan_out = (filter_shape[0] * np.prod(filter_shape[2:]) / 
                           k) # it's 
                
                W_bound = np.sqrt(6. / (fan_in + fan_out))
                
                W_val = np.asarray(
                    rng.uniform(low=-W_bound, high=W_bound, size=filter_shape),
                    dtype=theano.config.floatX
                )
            else:
                # normal initialization
                W_val = np.asarray(
                    rng.normal(0, 0.05, size = filter_shape),
                    dtype=theano.config.floatX
                )

            self.W = theano.shared(
                value = np.asarray(W_val,
                                   dtype = theano.config.floatX),
                name = "W",
                borrow=True
            )
        
        # make b
        if b is not None:
            b_val = b
            b_size = b.shape
            self.b = b
        else:
            b_size = (filter_shape[0], )
            b_val = np.zeros(b_size)
            
            self.b = theano.shared(
                value = np.asarray(
                    b_val,
                    dtype = theano.config.floatX
                ),
                name = "b",
                borrow = True
            )

        self.params = [self.W, self.b]
        self.param_shapes = [filter_shape,
                             b_size ]

    def fold(self, x):
        """
        :type x: theano.tensor.tensor4
        """
        return (x[:, :, T.arange(0, x.shape[2], 2)] + 
                x[:, :, T.arange(1, x.shape[2], 2)]) / 2
        
    def k_max_pool(self, x, k):
        """
        perform k-max pool on the input along the rows

        input: theano.tensor.tensor4
           
        k: theano.tensor.iscalar
            the k parameter

        Returns: 
        4D tensor
        """
        ind = T.argsort(x, axis = 3)

        sorted_ind = T.sort(ind[:,:,:, -k:], axis = 3)
        
        dim0, dim1, dim2, dim3 = sorted_ind.shape
        
        indices_dim0 = T.arange(dim0).repeat(dim1 * dim2 * dim3)
        indices_dim1 = T.arange(dim1).repeat(dim2 * dim3).reshape((dim1*dim2*dim3, 1)).repeat(dim0, axis=1).T.flatten()
        indices_dim2 = T.arange(dim2).repeat(dim3).reshape((dim2*dim3, 1)).repeat(dim0 * dim1, axis = 1).T.flatten()
        
        return x[indices_dim0, indices_dim1, indices_dim2, sorted_ind.flatten()].reshape(sorted_ind.shape)
        
    @property
    def output(self):
        # non-linear transform of the convolution output
        conv_out = T.nnet.conv.conv2d(self.input, 
                                      self.W, 
                                      border_mode = "full")             

        if self.fold:
            # fold
            fold_out = self.fold(conv_out)
        else:
            fold_out = conv_out

        # k-max pool        
        pool_out = (self.k_max_pool(fold_out, self.k) + 
                    self.b.dimshuffle('x', 0, 'x', 'x'))
        
        # pool_out = theano.printing.Print("pool_out, self.k: %d" %self.k)(pool_out)
        
        # around 0.
        # why tanh becomes extreme?
        
        if self.activation == "tanh":
            # return theano.printing.Print("tanh(pool_out)")(T.tanh(pool_out))
            return T.tanh(pool_out)
        elif self.activation == "sigmoid":
            return T.nnet.sigmoid(pool_out)
        else:
            return T.switch(pool_out > 0, pool_out, 0)

class DropoutLayer(object):
    """
    As the name suggests

    Refer to here: https://github.com/mdenil/dropout/blob/master/mlp.py
    """

    def __init__(self, input, rng, dropout_rate):

        srng = theano.tensor.shared_randomstreams.RandomStreams(
            rng.randint(999999))
        
        # p=1-p because 1's indicate keep and p is prob of dropping
        mask = srng.binomial(n=1, 
                             p=1-dropout_rate, 
                             size=input.shape)

        self.output = input * T.cast(mask, theano.config.floatX)

def train_and_test(
        use_pretrained_embedding = False,
        fold_flags = [1,1],
        use_L2_reg = True,
        learning_rate = 0.1,
        fan_in_fan_out = True,
        delay_embedding_learning = True,
        embedding_learning_delay_epochs = 10,
        conv_activation_unit = "tanh", 
        epsilon = 0.000001,
        rho = 0.95,
        gamma = 0.1,
        embed_dm = 48,        
        ks = [15, 6],
        L2_regs= [0.00001, 0.0003, 0.0003, 0.0001],
        n_hidden = 500,
        batch_size = 500,
        n_epochs = 200, 
        dropout_switches = [True, True, True], 
        dropout_rates = [0.2, 0.5, 0.5],
        conv_layer_n = 2,
        nkerns = [6, 12],
        conv_sizes = [10, 7],
        print_config = {}
):

    assert conv_layer_n == len(conv_sizes) == len(nkerns) == (len(L2_regs) - 2) == len(fold_flags) == len(ks)

    ###################
    # get the data    #
    ###################
    datasets = util.stanford_sentiment('data/stanfordSentimentTreebank/trees/processed.pkl',
                                       corpus_folder = 'data/stanfordSentimentTreebank/trees/')
    
    train_set_x, train_set_y = datasets[0]
    valid_set_x, valid_set_y = datasets[1]
    test_set_x, test_set_y = datasets[2]
    word2index = datasets[3]
    index2word = datasets[4]

    n_train_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size
    train_sent_len = train_set_x.get_value(borrow=True).shape[1]
    possible_labels =  set(train_set_y.get_value().tolist())
    
    
    ###################################
    # Symbolic variable definition    #
    ###################################
    x = T.imatrix('x') # the word indices matrix
    sent_len = x.shape[1]
    y = T.ivector('y') # the sentiment labels

    batch_index = T.iscalar('batch_index')
    
    rng = np.random.RandomState(1234)        
        
    ###############################
    # Load pretrained embedding   #
    ###############################    
    pretrained_embeddings = theano.shared(
        value = np.asarray(
            np.load("data/stanfordSentimentTreebank/trees/pretrained.npy"), 
            dtype=theano.config.floatX
        ),
        name = "embeddings", 
        borrow = True,
    )
    
    ###############################
    # Construction of the network #
    ###############################

    # Layer 1, the embedding layer
    layer1 = WordEmbeddingLayer(rng, 
                                input = x, 
                                vocab_size = len(word2index), 
                                embed_dm = embed_dm, 
                                embeddings = (
                                    pretrained_embeddings 
                                    if use_pretrained_embedding else None))
    
    dropout_layers = [layer1]
    layers = [layer1]
    
    for i in xrange(conv_layer_n):
        fold_flag = fold_flags[i]
        
        # for the dropout layer
        dpl = DropoutLayer(
            input = dropout_layers[-1].output,
            rng = rng, 
            dropout_rate = dropout_rates[0]
        ) 
        next_layer_dropout_input = dpl.output
        next_layer_input = layers[-1].output
        
        # for the conv layer
        filter_shape = (
            nkerns[i],
            (1 if i == 0 else nkerns[i-1]), 
            1, 
            conv_sizes[i]
        )
        
        k = ks[i]
        # k = int(max(k_top, 
        #             math.ceil((conv_layer_n - float(i+1)) / conv_layer_n * train_sent_len)))
        
        print "For conv layer(%s) %d, filter shape = %r, k = %d, dropout_rate = %f and fan_in_fan_out: %r and fold: %d" %(
            conv_activation_unit, 
            i+2, 
            filter_shape, 
            k, 
            dropout_rates[i], 
            fan_in_fan_out, 
            fold_flag
        )
        
        # we have two layers adding to two paths repsectively, 
        # one for training
        # the other for prediction(averaged model)

        dropout_conv_layer = ConvFoldingPoolLayer(rng, 
                                                  input = next_layer_dropout_input,
                                                  filter_shape = filter_shape, 
                                                  k = k, 
                                                  fan_in_fan_out = fan_in_fan_out,
                                                  fold = fold_flag,
                                                  activation = conv_activation_unit)
    
        # for prediction
        # sharing weight with dropout layer
        conv_layer = ConvFoldingPoolLayer(rng, 
                                          input = next_layer_input,
                                          filter_shape = filter_shape,
                                          k = k,
                                          activation = conv_activation_unit,
                                          fold = fold_flag,
                                          W = dropout_conv_layer.W * (1 - dropout_rates[i]), # model averaging
                                          b = dropout_conv_layer.b
        )

        dropout_layers.append(dropout_conv_layer)
        layers.append(conv_layer)
    
    # last, the output layer
    # both dropout and without dropout
    if sum(fold_flags) > 0:
        n_in = nkerns[-1] * ks[-1] * embed_dm / (sum(fold_flags)*2)
    else:
        n_in = nkerns[-1] * ks[-1] * embed_dm
        
    print "For output layer, n_in = %d, dropout_rate = %f" %(n_in, dropout_rates[-1])
    
    dropout_output_layer = LogisticRegression(
        rng,
        input = dropout_layers[-1].output.flatten(2), 
        n_in = n_in, # divided by 2x(how many times are folded)
        n_out = len(possible_labels) # five sentiment level
    )

    output_layer = LogisticRegression(
        rng,
        input = layers[-1].output.flatten(2), 
        n_in = n_in,
        n_out = len(possible_labels),
        W = dropout_output_layer.W * (1 - dropout_rates[-1]), # sharing the parameters, don't forget
        b = dropout_output_layer.b
    )
    
    dropout_layers.append(dropout_output_layer)
    layers.append(output_layer)

    ###############################
    # Error and cost              #
    ###############################
    # cost and error come from different model!
    dropout_cost = dropout_output_layer.nnl(y)
    errors = output_layer.errors(y)
    
    def get_L2_sqr(param_layers):
        return T.sum([
            L2_reg / 2 * ((layer.W if hasattr(layer, "W") else layer.embeddings) ** 2).sum()
            for L2_reg, layer in zip(L2_regs, param_layers)
        ])
    L2_sqr = get_L2_sqr(dropout_layers)
    L2_sqr_no_ebd = get_L2_sqr(dropout_layers[1:])
    
    if use_L2_reg:
        cost = dropout_cost + L2_sqr
        cost_no_ebd = dropout_cost + L2_sqr_no_ebd
    else:
        cost = dropout_cost
    
    ###############################
    # Parameters to be used       #
    ###############################
    if not delay_embedding_learning:
        print "Immediate embedding learning. "
        embedding_learning_delay_epochs = 0        
    else:
        print "Delay embedding learning by %d epochs" %(embedding_learning_delay_epochs)
        
    print "param_layers: %r" %dropout_layers
    param_layers = dropout_layers
    
    ##############################
    # Parameter Update           #
    ##############################
    print "Using AdaDelta with rho = %f and epsilon = %f" %(rho, epsilon)
    
    params = [param for layer in param_layers for param in layer.params]
    param_shapes=  [param for layer in param_layers for param in layer.param_shapes]                                
    
    param_grads = [T.grad(cost, param) for param in params]

    # AdaDelta parameter update
    # E[g^2]
    # initialized to zero
    egs = [
        theano.shared(
            value = np.zeros(param_shape,
                             dtype = theano.config.floatX
                         ),
            borrow = True,        
            name = "Eg:" + param.name
        )
        for param_shape, param in zip(param_shapes, params)
    ]
    
    # E[\delta x^2], initialized to zero
    exs = [
        theano.shared(
            value = np.zeros(param_shape,
                             dtype = theano.config.floatX
                         ),
            borrow = True,        
            name = "Ex:" + param.name
        )
        for param_shape, param in zip(param_shapes, params)
    ]        
    
    new_egs = [
        rho * eg + (1 - rho) * g ** 2
        for eg, g in zip(egs, param_grads)
    ]
        
    delta_x = [
        -(T.sqrt(ex + epsilon) / T.sqrt(new_eg + epsilon)) * g
        for new_eg, ex, g in zip(new_egs, exs, param_grads)
    ]    
    
    new_exs = [
        rho * ex + (1 - rho) * (dx ** 2)
        for ex, dx in zip(exs, delta_x)
    ]    
    
    egs_updates = zip(egs, new_egs)
    exs_updates = zip(exs, new_exs)
    param_updates = [
        (p, p + dx)
        for dx, g, p in zip(delta_x, param_grads, params)
    ]

    updates = egs_updates + exs_updates + param_updates
    print "updates:\n", updates
    
    # updates without embedding
    # exclude the first parameter
    egs_updates_no_ebd = zip(egs[1:], new_egs[1:])
    exs_updates_no_ebd = zip(exs[1:], new_exs[1:])
    param_updates_no_ebd = [
        (p, p + dx)
        for dx, g, p in zip(delta_x, param_grads, params)[1:]
    ]
    updates_no_emb = egs_updates_no_ebd + exs_updates_no_ebd + param_updates_no_ebd
    
    print "updates_no_emb:\n", updates_no_emb
    
    def make_train_func(cost, updates):
        return theano.function(inputs = [batch_index],
                               outputs = [cost], 
                               updates = updates,
                               givens = {
                                   x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
                                   y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
                               },
        )        

    train_model_no_ebd = make_train_func(cost_no_ebd, updates_no_emb)
    train_model = make_train_func(cost, updates)

    def make_error_func(x_val, y_val):
        return theano.function(inputs = [],
                               outputs = errors, 
                               givens = {
                                   x: x_val,
                                   y: y_val
                               }, 
                           )
        
    train_error = make_error_func(train_set_x, train_set_y)

    valid_error = make_error_func(valid_set_x, valid_set_y)
    

    #############################
    # Debugging purpose code    #
    #############################
    # : PARAMETER TUNING NOTE:
    # some demonstration of the gradient vanishing probelm
    
    train_data_at_index = {
        x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
    }

    train_data_at_index_with_y = {
        x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
        y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
    }

    if print_config["nnl"]:
        get_nnl = theano.function(
            inputs = [batch_index],
            outputs = dropout_cost,
            givens = {
                x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
                y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
            }
        )
        
    if print_config["L2_sqr"]:
        get_L2_sqr = theano.function(
            inputs = [],
            outputs = L2_sqr
        )
        
    if print_config["grad_abs_mean"]:
        print_grads = theano.function(
            inputs = [], 
            outputs = [theano.printing.Print(param.name)(
                T.mean(T.abs_(param_grad))
            )
                       for param, param_grad in zip(params, param_grads)
                   ], 
            givens = {
                x: train_set_x,
                y: train_set_y
            }
        )

    activations = [
        l.output
        for l in dropout_layers[1:-1]
    ]
    weight_grads = [
        T.grad(cost, l.W)
        for l in dropout_layers[1:-1]
    ]

    if print_config["activation_hist"]:
        # turn into 1D array
        get_activations = theano.function(
            inputs = [batch_index], 
            outputs = [
                val.flatten(1)
                for val in activations
            ], 
            givens = train_data_at_index
        )

    if print_config["weight_grad_hist"]:
        # turn into 1D array
        get_weight_grads = theano.function(
            inputs = [batch_index], 
            outputs = [
                val.flatten(1)
                for val in weight_grads
            ], 
            givens = train_data_at_index_with_y
        )
        
    if print_config["activation_tracking"]:
        # get the mean and variance of activations for each conv layer                
        
        get_activation_mean = theano.function(
            inputs = [batch_index], 
            outputs = [
                T.mean(val)
                for val in activations
            ], 
            givens = train_data_at_index
        )

        get_activation_std = theano.function(
            inputs = [batch_index], 
            outputs = [
                T.std(val)
                for val in activations
            ], 
            givens = train_data_at_index
        )


    if print_config["weight_grad_tracking"]:
        # get the mean and variance of activations for each conv layer
        get_weight_grad_mean = theano.function(
            inputs = [batch_index], 
            outputs = [
                T.mean(g)
                for g in weight_grads
            ], 
            givens = train_data_at_index_with_y
        )

        get_weight_grad_std = theano.function(
            inputs = [batch_index], 
            outputs = [
                T.std(g)
                for g in weight_grads
            ], 
            givens = train_data_at_index_with_y
        )
        
    if print_config["adadelta_lr_mean"]:
        print_adadelta_lr_mean = theano.function(
            inputs = [],
            outputs = [
                theano.printing.Print("adadelta mean:" +eg.name)(
                    T.mean(T.sqrt(ex + epsilon) / T.sqrt(eg + epsilon))
                )
                for eg, ex in zip(egs, exs)
            ]
        )

    if print_config["adagrad_lr_mean"]:
        print_adagrad_lr_mean = theano.function(
            inputs = [],
            outputs = [
                theano.printing.Print("adagrad mean")(
                    T.mean(sq)
                )
                for sq in sqs
            ]
        )
        
    if print_config["embeddings"]:
        print_embeddings = theano.function(
            inputs = [],
            outputs = theano.printing.Print("embeddings")(layers[0].embeddings)
        )
    
    if print_config["logreg_W"]:
        print_logreg_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print(layers[-1].W.name)(layers[-1].W)
        )
        
    if print_config["logreg_b"]:
        print_logreg_b = theano.function(
            inputs = [],
            outputs = theano.printing.Print(layers[-1].b.name)(layers[-1].b)
        )

    if print_config["conv_layer1_W"]:
        print_convlayer1_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print("conv_l1.W")(layers[1].W)
        )

    if print_config["conv_layer2_W"]:
        print_convlayer2_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print("conv_l2.W")(layers[2].W)
        )

    if print_config["p_y_given_x"]:
        print_p_y_given_x = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("p_y_given_x")(layers[-1].p_y_given_x),
            givens = train_data_at_index
        )

    if print_config["l1_output"]:
        print_l1_output = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("l1_output")(layers[0].output),
            givens = train_data_at_index
        )


    if print_config["dropout_l1_output"]:
        print_dropout_l1_output = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("dropout_l1_output")(dropout_layers[0].output),
            givens = train_data_at_index
        )
        
    if print_config["l2_output"]:
        print_l2_output = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("l2_output")(layers[1].output),
            givens = train_data_at_index
        )

    if print_config["dropout_l2_output"]:
        print_dropout_l2_output = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("dropout_l2_output")(dropout_layers[1].output),
            givens = train_data_at_index
        )

    if print_config["l3_output"]:
        print_l3_output = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("l3_output")(layers[2].output),
            givens = train_data_at_index
        )
        
    param_n = sum([1. for l in param_layers for p in l.params])
    if print_config["param_weight_mean"]:
        print_param_weight_mean = theano.function(
            inputs = [], 
            outputs = [theano.printing.Print("weight mean:" + p.name)(
                T.mean(T.abs_(p))
            )
                       for l in param_layers
                       for p in l.params]
        )

    
    #the training loop
    patience = 10000  # look as this many examples regardless
    patience_increase = 2  # wait this much longer when a new best is
                                  # found
    improvement_threshold = 0.995  # a relative improvement of this much is
    # considered significant
                                  
    validation_frequency = min(n_train_batches, patience / 2)

    best_validation_loss = np.inf
    best_iter = 0

    start_time = time.clock()
    done_looping = False
    epoch = 0
    
    nnls = []
    L2_sqrs = []
    
    activation_means = [[] for i in xrange(conv_layer_n)]
    activation_stds = [[] for i in xrange(conv_layer_n)]
    weight_grad_means = [[] for i in xrange(conv_layer_n)]
    weight_grad_stds = [[] for i in xrange(conv_layer_n)]
    activation_hist_data = [[] for i in xrange(conv_layer_n)]
    weight_grad_hist_data = [[] for i in xrange(conv_layer_n)]
    try:
        while (epoch < n_epochs) and (not done_looping):
            epoch += 1
            print "At epoch {0}".format(epoch)

            if epoch == (embedding_learning_delay_epochs + 1):
                print "########################"
                print "Start training embedding"
                print "########################"

            # shuffle the training data        
            train_set_x_data = train_set_x.get_value(borrow = True)
            train_set_y_data = train_set_y.get_value(borrow = True)        
            
            permutation = np.random.permutation(train_set_x.get_value(borrow=True).shape[0])

            train_set_x.set_value(train_set_x_data[permutation])
            train_set_y.set_value(train_set_y_data[permutation])
            for minibatch_index in xrange(n_train_batches):
                if epoch >= (embedding_learning_delay_epochs + 1):
                    train_cost = train_model(minibatch_index)
                else:
                    train_cost = train_model_no_ebd(minibatch_index)

                if print_config["nnl"]:
                    nnl = get_nnl(minibatch_index)
                    # print "nll for batch %d: %f" %(minibatch_index, nnl)
                    nnls.append(nnl)
                    
                if print_config["L2_sqr"]:
                    L2_sqrs.append(get_L2_sqr())            
                    
                if print_config["activation_tracking"]:
                    layer_means = get_activation_mean(minibatch_index)
                    layer_stds = get_activation_std(minibatch_index)
                    for layer_ms, layer_ss, layer_m, layer_s in zip(activation_means, activation_stds, layer_means, layer_stds):
                        layer_ms.append(layer_m)
                        layer_ss.append(layer_s)

                if print_config["weight_grad_tracking"]:
                    layer_means = get_weight_grad_mean(minibatch_index)
                    layer_stds = get_weight_grad_std(minibatch_index)
                    
                    for layer_ms, layer_ss, layer_m, layer_s in zip(weight_grad_means, weight_grad_stds, layer_means, layer_stds):
                        layer_ms.append(layer_m)
                        layer_ss.append(layer_s)

                if print_config["activation_hist"]:
                    for layer_hist, layer_data in zip(activation_hist_data , get_activations(minibatch_index)):
                        layer_hist += layer_data.tolist()

                if print_config["weight_grad_hist"]:
                    for layer_hist, layer_data in zip(weight_grad_hist_data , get_weight_grads(minibatch_index)):
                        layer_hist += layer_data.tolist()
                
                # print_grads(minibatch_index)
                # print_learning_rates()

                if print_config["embeddings"]:
                    print_embeddings()

                # print_logreg_param()
                
                # iteration number
                iter = (epoch - 1) * n_train_batches + minibatch_index

                if (minibatch_index+1) % 50 == 0 or minibatch_index == n_train_batches - 1:
                    print "%d / %d minibatches completed" %(minibatch_index + 1, n_train_batches)                
                    if print_config["nnl"]:
                        print "`nnl` for the past 50 minibatches is %f" %(np.mean(np.array(nnls)))
                        nnls = []
                    if print_config["L2_sqr"]:
                        print "`L2_sqr`` for the past 50 minibatches is %f" %(np.mean(np.array(L2_sqrs)))
                        L2_sqrs = []                                    

                    if print_config["conv_layer1_W"]:
                        print_convlayer1_W()

                    if print_config["conv_layer2_W"]:
                        print_convlayer2_W()

                    if print_config["p_y_given_x"]:
                        print_p_y_given_x(minibatch_index)

                    if print_config["l1_output"]:
                        print_l1_output(minibatch_index)

                    if print_config["l2_output"]:
                        print_l2_output(minibatch_index)

                    if print_config["dropout_l1_output"]:
                        print_dropout_l1_output(minibatch_index)

                    if print_config["dropout_l2_output"]:
                        print_dropout_l2_output(minibatch_index)

                    if print_config["l3_output"]:
                        print_l3_output(minibatch_index)

                if (iter + 1) % validation_frequency == 0:
                    if print_config["param_weight_mean"]:
                        print_param_weight_mean()

                    if print_config["adadelta_lr_mean"]:
                        print_adadelta_lr_mean()

                    if print_config["adagrad_lr_mean"]:
                        print_adagrad_lr_mean()

                    if print_config["grad_abs_mean"]:
                        print_grads()
                    
                    if print_config["logreg_W"]:
                        print_logreg_W()

                    if print_config["logreg_b"]:
                        print_logreg_b()

                    print "At epoch %d and minibatch %d. \nTrain error %.2f%%\nDev error %.2f%%\n" %(
                        epoch, 
                        minibatch_index,
                        train_error() * 100, 
                        valid_error() * 100
                    )
    except KeyboardInterrupt:
        from plot_util import plot_hist, plot_track, plt
        if print_config["activation_tracking"]:
            plot_track(activation_means, 
                          activation_stds, 
                          "activation_tracking")

        if print_config["weight_grad_tracking"]:
            plot_track(weight_grad_means, 
                          weight_grad_stds,
                          "weight_grad_tracking")
            
        if print_config["activation_hist"]:        
            plot_hist(activation_hist_data, "activation_hist")

        if print_config["weight_grad_hist"]:
            print len(weight_grad_hist_data[0]), len(weight_grad_hist_data[1])
            print weight_grad_hist_data[0][:10], weight_grad_hist_data[1][:10]
            plot_hist(weight_grad_hist_data, "weight_grad_hist")

        plt.show()
    
    end_time = time.clock()
    print(('Optimization complete. Best validation score of %f %% '
           'obtained at iteration %i, with test performance %f %%') %
          (best_validation_loss * 100., best_iter + 1, test_score * 100.))
    print >> sys.stderr, ('The code for file ' +
                          os.path.split(__file__)[1] +
                          ' ran for %.2fm' % ((end_time - start_time) / 60.))
    
if __name__ == "__main__":
    print_config = {
        "adadelta_lr_mean": 0,
        "adagrad_lr_mean": 0,
        
        "embeddings": 0,
        "logreg_W": 0,
        "logreg_b": 0,
        
        "conv_layer1_W": 0,
        "conv_layer2_W": 0,
        
        "activation_tracking": 0, # the activation value, mean and variance
        "weight_grad_tracking": 0, # the weight gradient tracking
        "backprop_grad_tracking": 0, # the backpropagated gradient, mean and variance. In this case, grad propagated from layer 2 to layer 1
        "activation_hist": 0, # the activation value, mean and variance
        "weight_grad_hist": 0, # the weight gradient tracking
        "backprop_grad_hist": 0,
        
        "l1_output": 0,
        "dropout_l1_output": 0,
        "l2_output": 0,        
        "dropout_l2_output": 0,
        "l3_output": 0,

        "p_y_given_x": 0,
        
        "grad_abs_mean": 0,
        "nnl": 1,
        "L2_sqr": 1,
        "param_weight_mean": 0,
    }
    

    train_and_test(
        use_pretrained_embedding = True,
        fold_flags =  [0, 0],
        use_L2_reg = True, 
        L2_regs= np.array([0.00001, 0.0003, 0.0003, 0.0001]) * 10,
        fan_in_fan_out = True,
        delay_embedding_learning = True,
        embedding_learning_delay_epochs = 4,
        conv_activation_unit = "tanh", 
        learning_rate = 0.0001, 
        batch_size = 10, 
        print_config = print_config, 
        dropout_switches = [False, False, False], 
        dropout_rates = [0.5, 0.5, 0.5]
    )
        
